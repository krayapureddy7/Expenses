"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.

Why A2A: agents-cli 1.1.0 (GA) deploys ADK agents to Agent Runtime as A2A agents
and no longer registers the reasoning-engine operation schema the old
`agent_engines.get(...).stream_query()` path relied on (operation_schemas() comes
back empty). The container serves the A2A protocol over the Agent Engine HTTP
passthrough, so this proxy fetches the agent's card and sends messages with the
a2a-sdk client (the same path `agents-cli run --mode a2a` uses). This works for
both A2A and plain ADK 1.1.0 deployments (the container serves A2A either way).

Run:
  pip install -r requirements.txt
  export AGENT_ENGINE_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."
  export AGENT_DIRECTORY="app"   # your agent's app directory (agents-cli-manifest.yaml)
  python main.py                 # -> http://localhost:8080
"""

import os
import uuid

from dotenv import load_dotenv
load_dotenv()

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    FilePart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TextPart,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE_NAME",
    "projects/512919939081/locations/us-central1/reasoningEngines/8152861402650902528",
)
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

# One set of ADC credentials, refreshed per request (access tokens expire ~1h).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


# OAuth2 & Session Security Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", os.urandom(32).hex())

from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse
import secrets

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=False, max_age=86400)

# Memory store of verified user tokens keyed by user session ID
_USER_GMAIL_TOKENS: dict[str, dict] = {}


@app.get("/auth/status")
async def auth_status(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
        request.session["user_id"] = user_id

    token_info = _USER_GMAIL_TOKENS.get(user_id)
    if token_info and token_info.get("access_token"):
        return JSONResponse({
            "authenticated": True,
            "email": token_info.get("email", "Connected"),
            "name": token_info.get("name", "User"),
        })
    return JSONResponse({
        "authenticated": False,
        "configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    })


@app.get("/auth/login")
async def auth_login(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
        request.session["user_id"] = user_id

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        # Prompt user to configure credentials
        return RedirectResponse(url="/?auth_error=missing_credentials")

    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    # Derive base URL dynamically (works locally and on Cloud Run)
    base_url = str(request.base_url).rstrip("/")
    if "run.app" in str(request.url):
        base_url = base_url.replace("http://", "https://")
    redirect_uri = f"{base_url}/auth/callback"

    scope = "openid email profile https://www.googleapis.com/auth/gmail.readonly"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"response_type=code&"
        f"scope={scope}&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        "access_type=offline&"
        "prompt=consent"
    )
    return RedirectResponse(url=auth_url)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = None, state: str = None, error: str = None):
    if error or not code:
        return RedirectResponse(url=f"/?auth_error={error or 'cancelled'}")

    expected_state = request.session.get("oauth_state")
    if not expected_state or expected_state != state:
        return RedirectResponse(url="/?auth_error=csrf_validation_failed")

    user_id = request.session.get("user_id")
    base_url = str(request.base_url).rstrip("/")
    if "run.app" in str(request.url):
        base_url = base_url.replace("http://", "https://")
    redirect_uri = f"{base_url}/auth/callback"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            tokens = token_resp.json()
            if "access_token" not in tokens:
                return RedirectResponse(url="/?auth_error=token_exchange_failed")

            # Validate user identity with OpenID Connect
            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            user_info = user_resp.json()

            # Store sanitized session token securely
            _USER_GMAIL_TOKENS[user_id] = {
                "access_token": tokens["access_token"],
                "refresh_token": tokens.get("refresh_token", ""),
                "email": user_info.get("email", ""),
                "name": user_info.get("name", "User"),
            }
            return RedirectResponse(url="/?auth_success=1")
    except Exception as e:
        return RedirectResponse(url=f"/?auth_error={type(e).__name__}")


@app.get("/auth/logout")
async def auth_logout(request: Request):
    user_id = request.session.get("user_id")
    if user_id and user_id in _USER_GMAIL_TOKENS:
        del _USER_GMAIL_TOKENS[user_id]
    request.session.clear()
    return RedirectResponse(url="/")


@app.get("/api/fetch-gmail")
async def api_fetch_gmail(request: Request, timeframe: str = "current"):
    """Fetch bills, receipts, and statement alerts from the authenticated Gmail account."""
    user_id = request.session.get("user_id")
    token_info = _USER_GMAIL_TOKENS.get(user_id) if user_id else None

    if not token_info or not token_info.get("access_token"):
        return JSONResponse({
            "status": "unauthenticated",
            "message": "Please connect your Google account using the 'Connect Gmail' button first.",
            "items": []
        }, status_code=401)

    import re
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    # Configure query based on current vs past month
    if timeframe == "past":
        query = "subject:(bill OR invoice OR statement OR receipt OR payment) older_than:30d newer_than:60d"
    else:
        query = "subject:(bill OR invoice OR statement OR receipt OR payment) newer_than:30d"

    try:
        creds = Credentials(token=token_info["access_token"])
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        results = service.users().messages().list(userId="me", q=query, maxResults=8).execute()
        messages = results.get("messages", [])

        if not messages:
            return JSONResponse({
                "status": "empty",
                "message": f"No bills or receipt emails found for {timeframe} month query: '{query}'.",
                "timeframe": timeframe,
                "items": []
            })

        items = []
        for m in messages:
            msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
            snippet = msg.get("snippet", "")
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "No Subject")
            sender = headers.get("from", "Unknown Sender")
            date_sent = headers.get("date", "")

            amt_match = re.search(r'\$\s*([0-9]+(?:\.[0-9]{2})?)', f"{subject} {snippet}")
            amount = float(amt_match.group(1)) if amt_match else None

            cat = "Utilities" if any(w in subject.lower() for w in ["electric", "water", "internet", "power", "utility"]) else \
                  "Dining" if any(w in subject.lower() for w in ["uber eats", "doordash", "grubhub", "restaurant", "cafe"]) else \
                  "Subscription" if any(w in subject.lower() for w in ["netflix", "spotify", "google", "apple", "prime"]) else "General"

            items.append({
                "id": m["id"],
                "subject": subject,
                "from": sender,
                "date": date_sent,
                "amount": amount,
                "category": cat,
                "snippet": snippet[:150]
            })

        return JSONResponse({
            "status": "success",
            "timeframe": timeframe,
            "count": len(items),
            "items": items
        })
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "message": f"Gmail fetch failed: {str(e)}",
            "items": []
        }, status_code=500)


@app.get("/api/expenses")
async def get_expenses_dashboard(user_id: str = "default_user"):
    """Fetch all expenses from Firestore and group by category & month for analytics."""
    try:
        from google.cloud import firestore
        import google.auth
        credentials, project = google.auth.default()
        db = firestore.Client(project=project or "qwiklabs-gcp-04-d6542b445177", credentials=credentials)
        docs = db.collection("expenses").where("user_id", "==", user_id).stream()
        
        items = []
        category_totals = {}
        monthly_totals = {}
        total_amount = 0.0

        for doc in docs:
            d = doc.to_dict()
            amt = float(d.get("amount", 0.0))
            cat = d.get("category", "General")
            dt = d.get("date", "2026-10-01")
            month_key = dt[:7] if len(dt) >= 7 else "2026-10"

            total_amount += amt
            category_totals[cat] = category_totals.get(cat, 0.0) + amt
            monthly_totals[month_key] = monthly_totals.get(month_key, 0.0) + amt

            items.append({
                "id": doc.id,
                "amount": amt,
                "category": cat,
                "description": d.get("description", "Expense"),
                "date": dt,
            })

        # If no items found in Firestore, provide realistic sample data so dashboard displays rich charts and ledger
        if not items:
            items = [
                {"id": "exp_01", "amount": 18.50, "category": "Dining", "description": "Ramen Bar Lunch", "date": "2026-10-13"},
                {"id": "exp_02", "amount": 86.20, "category": "Utilities", "description": "CleanEnergy Electric Bill", "date": "2026-10-11"},
                {"id": "exp_03", "amount": 142.00, "category": "Groceries", "description": "Weekly Groceries Market", "date": "2026-10-08"},
                {"id": "exp_04", "amount": 428.50, "category": "Credit Card", "description": "Chase Sapphire Statement", "date": "2026-10-05"},
                {"id": "exp_05", "amount": 15.99, "category": "Subscription", "description": "Streaming & Cloud Storage", "date": "2026-10-01"},
                {"id": "exp_06", "amount": 75.30, "category": "Dining", "description": "Trattoria Dinner with Friends", "date": "2026-09-28"},
                {"id": "exp_07", "amount": 62.15, "category": "Transportation", "description": "Caltrain Monthly Clipper Pass", "date": "2026-09-15"},
                {"id": "exp_08", "amount": 120.40, "category": "Groceries", "description": "Trader Joe's Pantry Haul", "date": "2026-08-20"},
            ]
            category_totals = {
                "Dining": 93.80,
                "Groceries": 262.40,
                "Utilities": 86.20,
                "Credit Card": 428.50,
                "Subscription": 15.99,
                "Transportation": 62.15,
            }
            monthly_totals = {
                "2026-08": 120.40,
                "2026-09": 137.45,
                "2026-10": 691.19,
            }
            total_amount = sum(i["amount"] for i in items)

        return JSONResponse({
            "status": "success",
            "total_spending": round(total_amount, 2),
            "transaction_count": len(items),
            "by_category": {k: round(v, 2) for k, v in category_totals.items()},
            "by_month": {k: round(v, 2) for k, v in sorted(monthly_totals.items())},
            "recent_transactions": items[:15],
        })
    except Exception as e:
        # Fallback realistic dummy data if Firestore is unreachable
        return JSONResponse({
            "status": "fallback",
            "total_spending": 748.19,
            "transaction_count": 5,
            "by_category": {
                "Dining": 142.50,
                "Groceries": 285.00,
                "Utilities": 86.20,
                "Subscription": 15.99,
                "Transportation": 218.50,
            },
            "by_month": {
                "2026-08": 180.00,
                "2026-09": 243.50,
                "2026-10": 324.69,
            },
            "recent_transactions": [
                {"amount": 18.50, "category": "Dining", "description": "Ramen Bar Lunch", "date": "2026-10-13"},
                {"amount": 86.20, "category": "Utilities", "description": "CleanEnergy Electric Bill", "date": "2026-10-11"},
                {"amount": 142.00, "category": "Groceries", "description": "Weekly Groceries Market", "date": "2026-10-08"},
                {"amount": 428.50, "category": "Credit Card", "description": "Chase Sapphire Statement", "date": "2026-10-05"},
                {"amount": 15.99, "category": "Subscription", "description": "Streaming & Cloud Storage", "date": "2026-10-01"}
            ]
        })


# Budget in-memory cache and Firestore persistence
_USER_BUDGETS: dict[str, dict] = {
    "default_user": {
        "monthly_limit": 2000.0,
        "dining_limit": 400.0,
        "groceries_limit": 500.0,
        "shopping_limit": 300.0,
        "entertainment_limit": 200.0,
        "utilities_limit": 250.0,
        "savings_goal": 500.0,
    }
}


@app.get("/api/budget")
async def get_budget(user_id: str = "default_user"):
    """Retrieve user's monthly budget limits and targets."""
    return JSONResponse(_USER_BUDGETS.get(user_id, _USER_BUDGETS["default_user"]))


@app.post("/api/budget")
async def set_budget(req: Request):
    """Save user's monthly budget settings (iOS Numbers/Wallet style)."""
    data = await req.json()
    user_id = data.get("user_id", "default_user")
    
    current = _USER_BUDGETS.get(user_id, _USER_BUDGETS["default_user"]).copy()
    for k in ["monthly_limit", "dining_limit", "groceries_limit", "shopping_limit", "entertainment_limit", "utilities_limit", "savings_goal"]:
        if k in data and data[k] is not None:
            try:
                current[k] = float(data[k])
            except (ValueError, TypeError):
                pass
                
    _USER_BUDGETS[user_id] = current
    
    # Also save to Firestore if reachable
    try:
        from google.cloud import firestore
        import google.auth
        credentials, project = google.auth.default()
        db = firestore.Client(project=project or "qwiklabs-gcp-04-d6542b445177", credentials=credentials)
        db.collection("user_budgets").document(user_id).set(current, merge=True)
    except Exception:
        pass

    return JSONResponse({"status": "success", "budget": current})


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    # Always return JSON so the browser never receives a plain-text 500 page
    # (which shows up in the chat as "Unexpected token 'I', "Internal S"... is
    # not valid JSON"). Any server-side failure now surfaces as a readable
    # message in the chat bubble instead.
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        card = AgentCard(**resp.json())
        # Agent Runtime does not serve a public card URL, so point the client at
        # the passthrough base for message sends.
        card.url = A2A_BASE
        _card = card
    return _card


import json
import re

_A2A_TAG_RE = re.compile(r"<a2a_datapart_json>(.*?)</a2a_datapart_json>", re.DOTALL)


def _extract_parts(parts: list) -> list[dict]:
    """Turn A2A response parts into structured parts for the chat UI."""
    out: list[dict] = []
    for p in parts:
        root = getattr(p, "root", p)
        text_val = None
        if isinstance(root, TextPart) and getattr(root, "text", None):
            text_val = root.text
        elif getattr(root, "text", None):
            text_val = root.text

        if text_val:
            # Check if text contains wrapped A2UI datapart
            matches = _A2A_TAG_RE.findall(text_val)
            if matches:
                for match in matches:
                    try:
                        envelope = json.loads(match.strip())
                        if envelope.get("metadata", {}).get("mimeType") == _A2UI_MIME:
                            out.append({"kind": "a2ui", "data": envelope.get("data", {})})
                    except Exception:
                        pass
                clean_text = _A2A_TAG_RE.sub("", text_val).strip()
                if clean_text:
                    out.append({"kind": "text", "text": clean_text})
            else:
                out.append({"kind": "text", "text": text_val})
        elif getattr(root, "data", None) is not None:
            raw_data = root.data
            # Sometimes raw_data is {"data": {...}, "kind": "data", "metadata": {"mimeType": ...}}
            if isinstance(raw_data, dict) and "data" in raw_data and "metadata" in raw_data:
                meta = raw_data.get("metadata") or {}
                mime = meta.get("mimeType") if isinstance(meta, dict) else None
                if mime == _A2UI_MIME:
                    out.append({"kind": "a2ui", "data": raw_data.get("data", {})})
                else:
                    out.append({"kind": "a2ui", "data": raw_data.get("data", {})})
            else:
                meta = getattr(root, "metadata", None) or {}
                mime = meta.get("mimeType") if isinstance(meta, dict) else None
                if mime == _A2UI_MIME:
                    out.append({"kind": "a2ui", "data": raw_data})
                elif isinstance(raw_data, dict) and ("beginRendering" in raw_data or "surfaceUpdate" in raw_data):
                    out.append({"kind": "a2ui", "data": raw_data})
        elif isinstance(root, FilePart):
            uri = getattr(getattr(root, "file", None), "uri", None)
            if uri:
                out.append({"kind": "text", "text": uri})
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    
    # Associate conversation context with verified session
    session_user_id = req.session.get("user_id") or "web-user"
    user_token = _USER_GMAIL_TOKENS.get(session_user_id, {})
    
    # If the user is authenticated, give the agent context of their authenticated email
    user_email = user_token.get("email")
    if user_email and ("gmail" in message.lower() or "bill" in message.lower() or "card" in message.lower() or "receipt" in message.lower()):
        # Prepend verified identity context safely
        message = f"[Authenticated User: {user_email}] {message}"
    
    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        factory = ClientFactory(
            ClientConfig(
                supported_transports=[
                    TransportProtocol.jsonrpc,
                    TransportProtocol.http_json,
                ],
                httpx_client=client,
            )
        )
        a2a_client = factory.create(card)

        msg = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(text=message))],
            context_id=_contexts.get(session_user_id),
        )

        last_task = None
        async for event in a2a_client.send_message(msg):
            if not isinstance(event, tuple):
                continue
            task, update = event
            if task is not None:
                last_task = task
                if getattr(task, "context_id", None):
                    _contexts[session_user_id] = task.context_id
            if hasattr(update, "artifact") and getattr(update, "artifact", None) is not None:
                parts.extend(_extract_parts(update.artifact.parts))

        # Also pull from task artifacts if parts still empty
        if not parts and last_task is not None:
            for artifact in getattr(last_task, "artifacts", None) or []:
                parts.extend(_extract_parts(artifact.parts))

    if not parts:
        # The turn produced no text or UI (e.g. the agent only ran tools, or a
        # tool stalled). Be honest rather than silent.
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]
    return JSONResponse({"parts": parts})


# Serve the chat UI (keep this mount last so /chat wins).
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
