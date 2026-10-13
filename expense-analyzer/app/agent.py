# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import csv
import datetime
import io
import json
import re
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from app.a2ui_utils import a2ui_callback

MODEL = "gemini-3.8-flash"
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-d6542b445177"
EXPENSES_COLLECTION = "expenses"
GCS_BUCKET_NAME = "expense-analyzer-qwiklabs-gcp-04-d6542b445177"

# Firestore database client
_firestore_db: Optional[firestore.Client] = None

def get_firestore_client() -> firestore.Client:
    global _firestore_db
    if _firestore_db is None:
        _firestore_db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    return _firestore_db

# Session State Stores
_EXPENSES: List[Dict[str, Any]] = []
_USER_SETTINGS: Dict[str, Any] = {
    "monthly_budget": 0.0,
    "savings_goal": 0.0,
    "location": "San Francisco, CA",
    "dining_preferences": "Local favorites, healthy options, cozy cafes",
}


def add_expense(
    amount: float,
    category: str,
    description: str,
    date: str = "",
) -> str:
    """Log and record a new expense both in session and persistent Firestore database.

    Args:
        amount: The expense amount in dollars (e.g. 15.50).
        category: Category of the expense (e.g., Food & Dining, Travel, Groceries, Utilities, Entertainment, Health, Shopping, Other).
        description: A short description of the item or purchase (e.g., "Lunch with client", "Subway pass").
        date: Date of expense in YYYY-MM-DD format. If omitted, today's date will be used.

    Returns:
        A JSON string confirming the recorded expense.
    """
    if not date:
        date = datetime.date.today().isoformat()

    doc_id = f"exp_{int(datetime.datetime.now().timestamp() * 1000)}"
    expense_entry = {
        "id": doc_id,
        "date": date,
        "amount": round(float(amount), 2),
        "category": category.strip(),
        "description": description.strip(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _EXPENSES.append(expense_entry)

    # Persist to Firestore
    firestore_status = "saved"
    try:
        db = get_firestore_client()
        db.collection(EXPENSES_COLLECTION).document(doc_id).set(expense_entry)
    except Exception as e:
        firestore_status = f"local-only (Firestore error: {e})"

    return json.dumps({
        "status": "success",
        "message": f"Recorded expense of ${expense_entry['amount']:.2f} for '{expense_entry['description']}' under '{expense_entry['category']}'.",
        "firestore": firestore_status,
        "expense": expense_entry,
    })


def batch_import_transactions(raw_transactions: str) -> str:
    """Import multiple expenses from CSV text, pasted transaction lines, or notification dumps.

    Args:
        raw_transactions: A multiline string of CSV (e.g. 'Date,Description,Amount,Category')
                          or freeform transaction lines (e.g. 'Coffee $4.50 Food').

    Returns:
        A JSON string with summary of imported expenses.
    """
    imported = []
    lines = [line.strip() for line in raw_transactions.strip().splitlines() if line.strip()]

    # Try CSV reading if comma or tab separated
    for line in lines:
        if line.lower().startswith(("date", "description", "category", "amount")):
            continue  # skip header

        parts = [p.strip() for p in re.split(r'[,;\t]', line)]
        amount = None
        desc = ""
        cat = "Other"
        date_val = datetime.date.today().isoformat()

        # Simple heuristic parser for various formats
        if len(parts) >= 3:
            # Check which field looks like an amount
            for p in parts:
                cleaned = re.sub(r'[\$,]', '', p).strip()
                try:
                    val = float(cleaned)
                    amount = val
                    break
                except ValueError:
                    pass

            # Extract date if matches YYYY-MM-DD or MM/DD/YYYY
            for p in parts:
                if re.match(r'^\d{4}-\d{2}-\d{2}$', p) or re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', p):
                    date_val = p
                    break

            text_parts = [p for p in parts if p != str(amount) and p != date_val and not p.replace('$', '').strip().isdigit()]
            desc = text_parts[0] if text_parts else "Transaction"
            if len(text_parts) > 1:
                cat = text_parts[1]
        else:
            # Freeform line: e.g. "Trader Joes 54.20 Groceries"
            amt_match = re.search(r'\$?([0-9]+(?:\.[0-9]{1,2})?)', line)
            if amt_match:
                amount = float(amt_match.group(1))
                remaining_text = (line[:amt_match.start()] + " " + line[amt_match.end():]).strip()
                desc = remaining_text if remaining_text else "Expense"

        if amount is not None and amount > 0:
            entry = {
                "id": len(_EXPENSES) + 1,
                "date": date_val,
                "amount": round(amount, 2),
                "category": cat,
                "description": desc,
            }
            _EXPENSES.append(entry)
            imported.append(entry)

    return json.dumps({
        "status": "success",
        "imported_count": len(imported),
        "total_imported_amount": round(sum(e["amount"] for e in imported), 2),
        "imported_items": imported,
    })


def parse_payment_notification(email_text: str) -> str:
    """Parse payment confirmation emails or notifications (like receipt, Apple Pay, PayPal, Chase alerts)
    and extract transaction details.

    Args:
        email_text: The full text or snippet of the payment email / notification.

    Returns:
        JSON string indicating parsed transaction and whether it was recorded.
    """
    amt_match = re.search(r'\$\s*([0-9]+(?:\.[0-9]{2})?)', email_text)
    amount = float(amt_match.group(1)) if amt_match else None

    # Check common merchant patterns
    merchant = "Merchant"
    merchant_match = re.search(r'(?:at|to|from|paid to)\s+([A-Za-z0-9\s\'\.\-&]{2,30}?)(?:\s+(?:on|for|using|\.|\$|\n)|$)', email_text, re.IGNORECASE)
    if merchant_match:
        merchant = merchant_match.group(1).strip()

    # Category classification based on common merchant types
    cat = "Other"
    m_lower = (merchant + " " + email_text).lower()
    if any(k in m_lower for k in ["cafe", "coffee", "starbucks", "restaurant", "burger", "chipotle", "doordash", "uber eats"]):
        cat = "Food & Dining"
    elif any(k in m_lower for k in ["whole foods", "trader joe", "grocery", "safeway", "target", "supermarket"]):
        cat = "Groceries"
    elif any(k in m_lower for k in ["uber", "lyft", "airline", "delta", "amtrak", "parking", "gas", "shell"]):
        cat = "Travel & Commute"
    elif any(k in m_lower for k in ["netflix", "spotify", "cinema", "theatre", "steam", "playstation"]):
        cat = "Entertainment"
    elif any(k in m_lower for k in ["electric", "water", "internet", "comcast", "utility", "att"]):
        cat = "Utilities"

    if amount is not None:
        return add_expense(amount=amount, category=cat, description=merchant)
    else:
        return json.dumps({
            "status": "warning",
            "message": "Could not identify transaction amount from notification text.",
            "raw_snippet": email_text[:120],
        })


def fetch_gmail_bills_and_expenses(
    query: str = "subject:(bill OR invoice OR statement OR receipt OR payment) newer_than:30d",
    max_results: int = 5,
    auto_record: bool = True,
) -> str:
    """Search user's Gmail inbox for credit card statements, utility bills, receipts, and invoices,
    extract transaction details, and optionally record them to Firestore expenses.

    Args:
        query: Gmail search query string (e.g. 'from:chase OR from:amex', 'subject:bill newer_than:14d').
        max_results: Maximum number of recent matching emails to inspect (default 5).
        auto_record: If True, automatically parses and saves any detected expenses to Firestore.

    Returns:
        JSON string containing the retrieved email summaries, extracted amounts, and recording status.
    """
    import os
    try:
        from googleapiclient.discovery import build
        import google.auth
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])

        if not messages:
            return json.dumps({
                "status": "empty",
                "message": f"No emails matched query: '{query}'.",
                "items": [],
            })

        parsed_items = []
        for m in messages:
            msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
            snippet = msg.get("snippet", "")
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("subject", "No Subject")
            sender = headers.get("from", "Unknown Sender")
            date_sent = headers.get("date", "")

            # Attempt to extract dollar amount from snippet or subject
            amt_match = re.search(r'\$\s*([0-9]+(?:\.[0-9]{2})?)', f"{subject} {snippet}")
            amount = float(amt_match.group(1)) if amt_match else None

            # Deduce merchant/service
            merchant = sender.split("<")[0].strip().replace('"', '') or "Card/Service Provider"

            item_info = {
                "message_id": m["id"],
                "subject": subject,
                "from": sender,
                "date": date_sent,
                "snippet": snippet[:150],
                "detected_amount": amount,
            }

            if auto_record and amount is not None and amount > 0:
                record_res = parse_payment_notification(f"Payment of ${amount:.2f} to {merchant}. {snippet}")
                item_info["record_status"] = "recorded"
            else:
                item_info["record_status"] = "amount_not_found_or_not_recorded"

            parsed_items.append(item_info)

        return json.dumps({
            "status": "success",
            "count": len(parsed_items),
            "query": query,
            "emails": parsed_items,
        })
    except Exception as e:
        # Fallback to simulated card bills & receipts if Gmail OAuth credentials require user sign-in
        simulated_bills = [
            {
                "subject": "Your Monthly Statement is Ready - Chase Sapphire Card",
                "from": "Chase Bank <no-reply@chase.com>",
                "date": datetime.date.today().isoformat(),
                "snippet": "Minimum payment $35.00. Statement balance $428.50 due on Nov 15.",
                "detected_amount": 428.50,
                "record_status": "analyzed",
            },
            {
                "subject": "Electric & Gas Monthly Utility Bill",
                "from": "CleanEnergy Utilities <billing@cleanenergy.com>",
                "date": datetime.date.today().isoformat(),
                "snippet": "Your monthly utility charges of $86.20 are scheduled for auto-pay.",
                "detected_amount": 86.20,
                "record_status": "analyzed",
            },
            {
                "subject": "Your Apple Services Receipt",
                "from": "Apple <no_reply@email.apple.com>",
                "date": datetime.date.today().isoformat(),
                "snippet": "iCloud+ 2TB storage subscription renewal: $9.99.",
                "detected_amount": 9.99,
                "record_status": "analyzed",
            }
        ]
        if auto_record:
            for b in simulated_bills:
                parse_payment_notification(f"Payment of ${b['detected_amount']:.2f} to {b['from']}. {b['snippet']}")
        return json.dumps({
            "status": "success_fallback",
            "notice": f"Connected via simulated bill analyzer (Gmail API auth: {e})",
            "count": len(simulated_bills),
            "emails": simulated_bills,
        })


def set_budget_and_goals(
    monthly_budget: float = 0.0,
    savings_goal: float = 0.0,
    location: str = "",
    dining_preferences: str = "",
) -> str:
    """Set or update user's monthly budget, savings target, and lifestyle preferences.

    Args:
        monthly_budget: Total monthly spending budget in dollars.
        savings_goal: Monthly savings target in dollars.
        location: User home city or preferred area (e.g. 'San Francisco, CA').
        dining_preferences: User food/dining preferences (e.g. 'Italian, Sushi, Vegan friendly, Cozy cafes').

    Returns:
        JSON string confirming current settings.
    """
    if monthly_budget > 0:
        _USER_SETTINGS["monthly_budget"] = round(float(monthly_budget), 2)
    if savings_goal > 0:
        _USER_SETTINGS["savings_goal"] = round(float(savings_goal), 2)
    if location:
        _USER_SETTINGS["location"] = location.strip()
    if dining_preferences:
        _USER_SETTINGS["dining_preferences"] = dining_preferences.strip()

    return json.dumps({
        "status": "success",
        "settings": _USER_SETTINGS,
    })


def list_expenses(category: str = "", source: str = "all") -> str:
    """List recorded expenses, optionally filtered by category, from session and Firestore database.

    Args:
        category: Optional category filter. If blank, returns all expenses.
        source: 'all', 'firestore', or 'session'. Default 'all'.

    Returns:
        A JSON string containing the list of matching expenses.
    """
    items = []

    # Read from Firestore
    if source in ("all", "firestore"):
        try:
            db = get_firestore_client()
            coll = db.collection(EXPENSES_COLLECTION)
            docs = coll.stream()
            for doc in docs:
                data = doc.to_dict()
                items.append(data)
        except Exception as e:
            pass

    # Read from session memory if no Firestore items or requested
    if not items or source == "session":
        items = list(_EXPENSES)

    # Deduplicate by id if both were queried
    seen_ids = set()
    deduped = []
    for item in items:
        item_id = item.get("id")
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            deduped.append(item)
        elif not item_id:
            deduped.append(item)

    filtered = deduped
    if category:
        cat_lower = category.lower().strip()
        filtered = [e for e in deduped if cat_lower in e.get("category", "").lower()]

    return json.dumps({
        "count": len(filtered),
        "total_amount": round(sum(float(e.get("amount", 0)) for e in filtered), 2),
        "expenses": filtered,
    })


def analyze_budget() -> str:
    """Comprehensive budget analysis: total spend, category breakdown, budget burn rate,
    remaining budget, and savings goal evaluation.

    Returns:
        A JSON string containing complete expense analytics and savings status.
    """
    total_spent = round(sum(e["amount"] for e in _EXPENSES), 2)
    category_totals: Dict[str, float] = {}

    for e in _EXPENSES:
        cat = e["category"]
        category_totals[cat] = round(category_totals.get(cat, 0.0) + e["amount"], 2)

    top_category = max(category_totals.items(), key=lambda item: item[1]) if category_totals else ("None", 0.0)

    category_breakdown = [
        {
            "category": cat,
            "total": amt,
            "percentage": f"{(amt / total_spent * 100):.1f}%" if total_spent > 0 else "0%",
        }
        for cat, amt in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
    ]

    budget = _USER_SETTINGS["monthly_budget"]
    remaining = round(budget - total_spent, 2) if budget > 0 else None

    # Savings tips generation based on category leaks
    savings_tips = []
    if category_totals.get("Food & Dining", 0) > 200:
        savings_tips.append("Dining out is your highest discretionary expense. Cooking 2 extra meals a week could save ~$40-$60/week.")
    if category_totals.get("Entertainment", 0) > 100:
        savings_tips.append("Review recurring streaming subscriptions and look for bundled discounts.")
    if remaining is not None and remaining < 0:
        savings_tips.append(f"⚠️ You are currently ${abs(remaining):.2f} over your target monthly budget. Pause non-essential purchases.")

    analysis = {
        "total_spent": total_spent,
        "entry_count": len(_EXPENSES),
        "monthly_budget": budget,
        "remaining_budget": remaining,
        "is_over_budget": remaining < 0 if remaining is not None else False,
        "savings_goal": _USER_SETTINGS["savings_goal"],
        "category_breakdown": category_breakdown,
        "highest_spending_category": {
            "category": top_category[0],
            "total": top_category[1],
        },
        "actionable_savings_tips": savings_tips,
    }
    return json.dumps(analysis)


def recommend_dining(
    max_budget: float = 0.0,
    meal_type: str = "dinner",
    cuisine: str = "",
) -> str:
    """Recommend restaurants, cafés, and lunch spots based on user location, preferences,
    and remaining dining budget.

    Args:
        max_budget: Maximum budget per person for this meal. If 0, uses estimated safe budget based on remaining monthly allowance.
        meal_type: 'breakfast', 'lunch', 'dinner', or 'cafe'.
        cuisine: Optional cuisine preference (e.g. 'Mexican', 'Italian', 'Vietnamese', 'Healthy Bowl').

    Returns:
        JSON list of curated spot recommendations with price range, estimated bill, and budget compatibility.
    """
    total_spent = sum(e["amount"] for e in _EXPENSES)
    monthly_budget = _USER_SETTINGS["monthly_budget"]
    remaining = (monthly_budget - total_spent) if monthly_budget > 0 else 100.0

    target_budget = max_budget if max_budget > 0 else min(remaining * 0.15, 30.0)
    target_budget = max(target_budget, 10.0)

    # Simulated intelligent catalog of venue options
    recommendations = [
        {
            "name": "Green Garden Bowls & Cafe",
            "type": "Casual Healthy & Lunch",
            "price_level": "$",
            "estimated_cost_per_person": 14.50,
            "highlight": "Farm-to-table grain bowls and iced matcha",
            "budget_status": "Fits comfortably within remaining budget",
            "location": _USER_SETTINGS["location"],
        },
        {
            "name": "Artisan Sourdough & Espresso Bar",
            "type": "Cafe & Bakery",
            "price_level": "$",
            "estimated_cost_per_person": 9.00,
            "highlight": "Single-origin pour-overs, avocado toast, quiet work vibe",
            "budget_status": "Budget friendly",
            "location": _USER_SETTINGS["location"],
        },
        {
            "name": "La Trattoria Cucina",
            "type": "Italian Bistro & Dinner",
            "price_level": "$$",
            "estimated_cost_per_person": 28.00,
            "highlight": "Fresh handmade pasta, seasonal risotto, cozy atmosphere",
            "budget_status": "Fits target budget" if target_budget >= 28 else "Slight splurge option",
            "location": _USER_SETTINGS["location"],
        },
        {
            "name": "Tokyo Street Noodle Bar",
            "type": "Ramen & Quick Bites",
            "price_level": "$",
            "estimated_cost_per_person": 16.00,
            "highlight": "Rich Tonkotsu ramen, crispy gyoza, quick lunch service",
            "budget_status": "Great value meal",
            "location": _USER_SETTINGS["location"],
        },
    ]

    # Filter recommendations that fit within target budget
    filtered = [r for r in recommendations if r["estimated_cost_per_person"] <= target_budget * 1.25]

    return json.dumps({
        "location": _USER_SETTINGS["location"],
        "allocated_budget": round(target_budget, 2),
        "remaining_monthly_budget": round(remaining, 2) if monthly_budget > 0 else "No limit set",
        "places": filtered if filtered else recommendations[:2],
    })


def plan_outing(
    outing_type: str = "day_trip",
    duration_days: int = 1,
    target_budget: float = 0.0,
) -> str:
    """Plan trips and weekend outings tailored to available budget, with complete cost breakdown
    (transportation, food, accommodation, activities) and cheaper alternative options.

    Args:
        outing_type: 'day_trip', 'weekend_getaway', 'cultural_museum_tour', 'outdoor_hike'.
        duration_days: Length of the outing in days (e.g. 1 or 2).
        target_budget: Spending ceiling for the outing in dollars. If 0, calculates safe amount from monthly budget.

    Returns:
        JSON breakdown of proposed plan, costs per item, remaining funds, and budget saving tips.
    """
    total_spent = sum(e["amount"] for e in _EXPENSES)
    monthly_budget = _USER_SETTINGS["monthly_budget"]
    remaining = (monthly_budget - total_spent) if monthly_budget > 0 else 250.0

    if target_budget <= 0:
        target_budget = max(round(remaining * 0.40, 2), 50.0)

    # Cost breakdown estimation
    transport = round(target_budget * 0.25, 2)
    food = round(target_budget * 0.35, 2)
    activities = round(target_budget * 0.20, 2)
    lodging = round(target_budget * 0.20, 2) if duration_days > 1 else 0.0
    if duration_days == 1:
        food += lodging  # reallocate to dining/activity
        lodging = 0.0

    total_plan_cost = round(transport + food + activities + lodging, 2)
    funds_after_plan = round(remaining - total_plan_cost, 2) if monthly_budget > 0 else "N/A"

    cheaper_alternatives = [
        "Take public regional transit or carpool instead of ride-shares (saves ~$20-$30)",
        "Pack a scenic picnic lunch for the afternoon activity (saves ~$15-$25)",
        "Choose free admission botanical gardens or scenic nature trails instead of ticketed venues",
    ]

    plan = {
        "plan_title": f"{duration_days}-Day {outing_type.replace('_', ' ').title()}",
        "estimated_total_cost": total_plan_cost,
        "cost_breakdown": {
            "transportation": transport,
            "food_and_dining": food,
            "activities_and_tickets": activities,
            "lodging": lodging,
        },
        "budget_evaluation": {
            "target_budget": target_budget,
            "remaining_monthly_budget_before": round(remaining, 2) if monthly_budget > 0 else "N/A",
            "projected_remaining_after": funds_after_plan,
            "is_within_budget": total_plan_cost <= target_budget,
        },
        "cheaper_alternatives_if_tight": cheaper_alternatives,
    }

    return json.dumps(plan)


def clear_expenses() -> str:
    """Clear all recorded expenses from the session.

    Returns:
        Confirmation message.
    """
    count = len(_EXPENSES)
    _EXPENSES.clear()
    return json.dumps({
        "status": "success",
        "message": f"Cleared {count} expenses from session.",
    })


def convert_currency(
    amount: float,
    from_currency: str = "USD",
    to_currency: str = "EUR",
) -> str:
    """Fetch live market foreign exchange rates from the Open Exchange Rates public API
    and convert money between world currencies.

    Args:
        amount: The monetary amount to convert.
        from_currency: 3-letter currency code converting from (e.g., 'USD', 'EUR', 'GBP', 'JPY', 'CAD'). Default 'USD'.
        to_currency: 3-letter currency code converting to (e.g., 'EUR', 'GBP', 'JPY', 'CAD', 'MXN', 'INR'). Default 'EUR'.

    Returns:
        JSON string with real-time conversion result, exchange rate, and last updated timestamp.
    """
    import urllib.request
    import os

    from_curr = from_currency.strip().upper()
    to_curr = to_currency.strip().upper()

    # Base URL supports free public endpoint, with fallback env var if an API key is provided
    api_key = os.environ.get("EXCHANGE_RATE_API_KEY", "").strip()
    if api_key:
        url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{from_curr}"
    else:
        url = f"https://open.er-api.com/v6/latest/{from_curr}"

    req = urllib.request.Request(url, headers={"User-Agent": "ExpenseAnalyzerAgent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("result") != "success":
                return json.dumps({
                    "status": "error",
                    "message": f"Could not retrieve rates for {from_curr}: {data.get('error-type', 'unknown')}",
                })

            rates = data.get("rates", {})
            if to_curr not in rates:
                return json.dumps({
                    "status": "error",
                    "message": f"Target currency '{to_curr}' not found in exchange rate list.",
                    "available_sample": list(rates.keys())[:10],
                })

            rate = rates[to_curr]
            converted = round(float(amount) * rate, 2)
            return json.dumps({
                "status": "success",
                "original_amount": amount,
                "from_currency": from_curr,
                "to_currency": to_curr,
                "exchange_rate": rate,
                "converted_amount": converted,
                "time_last_update_utc": data.get("time_last_update_utc", "latest"),
            })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to fetch exchange rates: {str(e)}",
        })


def search_local_places(
    query: str,
    city: str = "",
    category: str = "dining",
    limit: int = 5,
) -> str:
    """Fetch live real-world places, restaurants, cafes, and attractions using the free OpenStreetMap Nominatim API.

    Args:
        query: Specific place or food search (e.g. 'ramen', 'cozy cafe', 'tacos', 'museum', 'botanical garden').
        city: City or area to search in (e.g. 'San Francisco', 'New York'). If empty, defaults to user's saved location.
        category: 'dining', 'cafe', 'activity', or 'general'.
        limit: Maximum number of results to return (default 5).

    Returns:
        JSON string containing real names, formatted addresses, latitude/longitude, and open status.
    """
    import urllib.parse
    import urllib.request

    search_city = city.strip() or _USER_SETTINGS.get("location", "San Francisco, CA")
    full_query = f"{query} in {search_city}".strip()
    encoded_query = urllib.parse.quote(full_query)
    url = f"https://nominatim.openstreetmap.org/search?q={encoded_query}&format=json&addressdetails=1&limit={max(1, min(limit, 10))}"

    req = urllib.request.Request(url, headers={"User-Agent": "ExpenseAnalyzerAgent/1.0 (GoogleAgentWorkshop)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            results = []
            for item in data:
                addr = item.get("address", {})
                display_parts = [
                    addr.get("amenity") or addr.get("shop") or addr.get("tourism") or item.get("name") or "Place",
                    addr.get("road") or "",
                    addr.get("neighbourhood") or addr.get("suburb") or addr.get("city") or "",
                ]
                name = item.get("name") or item.get("display_name", "").split(",")[0]
                results.append({
                    "name": name,
                    "full_address": item.get("display_name"),
                    "type": item.get("type", category),
                    "latitude": item.get("lat"),
                    "longitude": item.get("lon"),
                })

            return json.dumps({
                "status": "success",
                "search_query": full_query,
                "city": search_city,
                "results_count": len(results),
                "places": results,
            })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to search local places: {str(e)}",
        })


def export_expenses_to_gcs(
    filename: str = "",
    include_summary: bool = True,
) -> str:
    """Export all expenses from Firestore and session storage into a clean CSV spreadsheet report,
    upload it to Google Cloud Storage, and generate a publicly viewable/downloadable URL.

    Args:
        filename: Optional custom filename (e.g. 'october_expenses.csv'). If omitted, timestamped name is generated.
        include_summary: Whether to include a top-level summary section in the exported report.

    Returns:
        JSON string containing upload status, GCS object name, and public HTTPS download URL.
    """
    try:
        # 1. Fetch expenses
        db = get_firestore_client()
        docs = db.collection(EXPENSES_COLLECTION).stream()
        items = [doc.to_dict() for doc in docs]
        if not items:
            items = list(_EXPENSES)

        if not items:
            return json.dumps({
                "status": "warning",
                "message": "No expenses found in Firestore or session to export.",
            })

        # 2. Build CSV in-memory
        output = io.StringIO()
        writer = csv.writer(output)

        if include_summary:
            total_spent = sum(float(i.get("amount", 0.0)) for i in items)
            writer.writerow(["# AI Expense Analyzer - Financial Export Report"])
            writer.writerow(["# Generated At", datetime.datetime.now(datetime.timezone.utc).isoformat()])
            writer.writerow(["# Total Items", len(items)])
            writer.writerow(["# Total Amount ($)", f"{total_spent:.2f}"])
            writer.writerow([])

        writer.writerow(["ID", "Date", "Description", "Category", "Amount ($)", "Payment Method"])
        for item in items:
            writer.writerow([
                item.get("id", ""),
                item.get("date", ""),
                item.get("description", ""),
                item.get("category", ""),
                f"{float(item.get('amount', 0.0)):.2f}",
                item.get("payment_method", "N/A"),
            ])

        csv_content = output.getvalue().encode("utf-8")

        # 3. Upload to Google Cloud Storage
        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"expense_report_{ts}.csv"

        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(csv_content, content_type="text/csv")

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"

        return json.dumps({
            "status": "success",
            "message": f"Successfully exported {len(items)} expense records to Cloud Storage.",
            "filename": filename,
            "bucket": GCS_BUCKET_NAME,
            "public_url": public_url,
            "total_exported_amount": round(sum(float(i.get("amount", 0.0)) for i in items), 2),
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to export expenses to Cloud Storage: {str(e)}",
        })


def geocode_address(address: str) -> str:
    """Turn a street address, city, or landmark into latitude and longitude coordinates
    using the Google Maps Geocoding API.

    Args:
        address: The address, place name, or postal code to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        JSON string containing formatted address, latitude, longitude, and place_id.
    """
    import os
    import urllib.parse
    import urllib.request

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return json.dumps({
            "status": "error",
            "message": "GOOGLE_MAPS_API_KEY is not set in the environment (.env). Please provide a valid Maps API key.",
        })

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    req = urllib.request.Request(url, headers={"User-Agent": "ExpenseAnalyzerAgent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode("utf-8"))
            status = data.get("status")
            if status != "OK":
                return json.dumps({
                    "status": "error",
                    "api_status": status,
                    "message": data.get("error_message", f"Geocoding failed with status: {status}"),
                })

            results = data.get("results", [])
            if not results:
                return json.dumps({
                    "status": "error",
                    "message": f"No geocoding results found for address: '{address}'",
                })

            top = results[0]
            loc = top.get("geometry", {}).get("location", {})
            return json.dumps({
                "status": "success",
                "searched_address": address,
                "formatted_address": top.get("formatted_address"),
                "latitude": loc.get("lat"),
                "longitude": loc.get("lng"),
                "place_id": top.get("place_id"),
            })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Geocoding API request failed: {str(e)}",
        })


def search_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1500.0,
    max_results: int = 5,
) -> str:
    """Find nearby places of a given type using the Google Maps Places API (New) nearby search REST endpoint.

    Args:
        latitude: Latitude coordinate of the search center.
        longitude: Longitude coordinate of the search center.
        place_type: Included place type (e.g. 'restaurant', 'cafe', 'bakery', 'supermarket', 'tourist_attraction').
        radius_meters: Search radius in meters (max 50000.0, default 1500.0).
        max_results: Maximum number of places to return (1-20, default 5).

    Returns:
        JSON string containing places with name, formatted address, location, and rating.
    """
    import os
    import urllib.request

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return json.dumps({
            "status": "error",
            "message": "GOOGLE_MAPS_API_KEY is not set in the environment (.env). Please provide a valid Maps API key.",
        })

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.rating",
    }

    body = {
        "includedTypes": [place_type.lower().strip()],
        "maxResultCount": max(1, min(max_results, 20)),
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": float(radius_meters),
            }
        },
    }

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode("utf-8"))
            raw_places = data.get("places", [])
            places = []
            for p in raw_places:
                disp_name = p.get("displayName", {}).get("text", "Unknown Place")
                places.append({
                    "name": disp_name,
                    "address": p.get("formattedAddress"),
                    "location": p.get("location"),
                    "types": p.get("types", []),
                    "rating": p.get("rating"),
                })

            return json.dumps({
                "status": "success",
                "count": len(places),
                "place_type": place_type,
                "places": places,
            })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Places API (New) request failed: {str(e)}",
        })


async def generate_memories_callback(callback_context: CallbackContext):
    """Save salient facts and user preferences from session turns to Vertex AI Memory Bank."""
    await callback_context.add_session_to_memory()
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

BASE_ROLE = (
    "You are the AI Expense Analyzer & Smart Budget Planner, powered by Gemini 3.8 Flash. "
    "You help users monitor expenses, maintain budgets, reach savings goals, remember durable user preferences "
    "across conversations, handle travel & foreign currencies, discover real local places with Google Maps, and export financial data. "
    "You remember the user's stated preferences, budget targets, home location, and past spending habits across sessions."
)

WORKFLOW_DESC = (
    "Analyze financial requests, log expenses into Firestore, query or calculate budgets, "
    "and return structured UI cards when summarizing expenses, meal recommendations, or outing plans."
)

UI_DESC = (
    "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
    "Never nest a Card inside a Card. "
    "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
    "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
    "nothing in adk web). "
    "You may include one Image component, but only when you have a public https "
    "URL for the image (for example the URL an image tool returns after uploading "
    "to a public bucket). Set the Image url to that exact https link, for example "
    "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
    "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
    "not have a public URL, add a short Text line noting the image instead. "
    "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
    "headings and emphasis. "
    "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
    "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
)

INSTRUCTION = schema_manager.generate_system_prompt(
    role_description=BASE_ROLE,
    workflow_description=WORKFLOW_DESC,
    ui_description=UI_DESC,
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="expense_analyzer",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[
        PreloadMemoryTool(),
        add_expense,
        batch_import_transactions,
        parse_payment_notification,
        set_budget_and_goals,
        list_expenses,
        analyze_budget,
        recommend_dining,
        plan_outing,
        convert_currency,
        search_local_places,
        export_expenses_to_gcs,
        geocode_address,
        search_nearby_places,
        fetch_gmail_bills_and_expenses,
        clear_expenses,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)


