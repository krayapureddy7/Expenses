# 💰 Smart Expense Analyzer & Financial Advisor

An agentic personal finance and expense analysis platform powered by **Google Gemini 2.5**, the **Agent Development Kit (ADK)**, and **Vertex AI Agent Engine**. Features real-time Firestore synchronization, automated Gmail bill and statement ingestion via OAuth 2.0, interactive analytics dashboards, and an Apple Numbers-style monthly budget management system.

---

## 🚀 Key Features

* 🤖 **Autonomous AI Financial Advisor**: Powered by Gemini on Vertex AI Reasoning Engine, handling multi-turn conversational queries, smart spend classification, and budget burn rate analytics.
* 📬 **Live Gmail Bill & Statement Ingestion**: Securely inspects user credit card statements, utility bills, and merchant charges via the Gmail API with token validation.
* 📊 **Interactive Analytics Dashboard**:
  * **Chart.js Doughnut (Pie) Chart**: Categorical segregation across Dining, Groceries, Utilities, Subscriptions, and Transport.
  * **Spending Velocity Bar Graph**: Month-over-month expenditure tracking.
  * **Live Financial Ledger**: Monospace-formatted real-time transaction ledger with automatic category tags and one-click Google Cloud Storage CSV export.
* 📱 **Apple Numbers & iOS Wallet Style Budget Controls**:
  * Fine-grained category target limits with interactive currency inputs.
  * Live dynamic budget allocation metrics: **Allocated Amount**, **Savings Ratio (%)**, and **Discretionary Buffer**.
  * Real-time ceiling violation warning indicators.
* 🗺️ **Google Maps Dining Recommendations**: Suggests budget-friendly restaurants and cafes matching financial goals and location preferences.
* 🗄️ **Multi-Service Cloud Persistence**:
  * **Google Cloud Firestore**: Schema-validated transactional storage.
  * **Google Cloud Storage (GCS)**: Automated export and archiving of expense reports.
  * **Vertex AI Memory Bank**: Cross-session long-term memory for user financial habits and constraints.
* 🪟 **Agent-to-User Interface (A2UI)**: Direct native card and table rendering without raw JSON formatting.

---

## 🛠️ Architecture & Tech Stack

### 🧠 AI & Agent Engine
| Technology | Role |
| :--- | :--- |
| **Google Gemini 2.5 Flash** | Core LLM reasoning model driving turn-by-turn conversation and tool selection |
| **Agent Development Kit (ADK)** | Orchestration framework for agent tools, callbacks, and state management |
| **Vertex AI Reasoning Engine** | Managed Agent Runtime hosting the serverless agent backend |
| **A2A Protocol & a2a-sdk** | Agent-to-Agent communication protocol bridging frontend and backend |
| **Vertex AI Memory Bank** | Long-term memory store maintaining context across user sessions |

### 🌐 Frontend & User Experience
| Technology | Role |
| :--- | :--- |
| **FastAPI Proxy Server** | Asynchronous gateway managing sessions, Google OAuth, and A2A streaming |
| **Chart.js (v4.4.2)** | High-performance canvas visualizations (Doughnut charts and Bar graphs) |
| **Apple Numbers Design System** | Glassmorphism, Material Symbols, and Plus Jakarta Sans typography |
| **Google Cloud Run** | Fully managed container deployment with auto-scaling and HTTPS |

### 🔌 Integrated APIs & Cloud Services
| API / Service | Purpose |
| :--- | :--- |
| **Gmail API (OAuth 2.0)** | Live scanning of credit card receipts, statements, and recurring bills |
| **Google Cloud Firestore** | NoSQL document database storing transactions and budget profiles |
| **Google Cloud Storage (GCS)** | Object storage bucket for automated CSV expense report exports |
| **Google Maps Geocoding & Places API** | Local restaurant discovery and budget-conscious dining alternatives |
| **Google Cloud Secret Manager** | Secure storage and retrieval of OAuth secrets and API credentials |

---

## 📂 Project Structure

```text
.
├── expense-analyzer/              # Agent Backend (ADK / Reasoning Engine)
│   ├── app/
│   │   ├── __init__.py
│   │   └── agent.py              # Gemini Agent definition, tools, and A2UI callbacks
│   ├── requirements.txt           # Python dependencies for the agent
│   └── agents-cli-manifest.yaml   # Deployment manifest for Vertex AI
│
├── frontend/                      # Web Application & Proxy
│   ├── static/
│   │   ├── index.html             # Multi-tab UI, Chart.js dashboard & iOS budget controls
│   │   └── favicon.ico
│   ├── Dockerfile                 # Cloud Run container definition
│   ├── main.py                    # FastAPI proxy, Google OAuth routes & Firestore APIs
│   └── requirements.txt           # Frontend dependencies (FastAPI, Authlib, httpx)
│
└── README.md                      # Project documentation
```

---

## 🚦 Local Setup & Running

### 1. Prerequisites
* Python 3.11+
* Google Cloud SDK (`gcloud` CLI)
* An active GCP Project with Vertex AI and Firestore enabled

### 2. Environment Configuration
Create a `.env` file in `frontend/`:
```bash
GCP_PROJECT="your-project-id"
GCP_REGION="us-central1"
AGENT_ENGINE_RESOURCE_NAME="projects/.../locations/us-central1/reasoningEngines/..."
GOOGLE_CLIENT_ID="your-google-oauth-client-id"
GOOGLE_CLIENT_SECRET="your-google-oauth-client-secret"
GOOGLE_MAPS_API_KEY="your-google-maps-api-key"
```

### 3. Run Frontend Locally
```bash
cd frontend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```
Open [http://localhost:8080](http://localhost:8080) in your browser.

---

## 🚢 Cloud Deployment

### Deploy Backend Agent to Vertex AI:
```bash
cd expense-analyzer
agents-cli deploy
```

### Deploy Frontend to Cloud Run:
```bash
gcloud run deploy expense-analyzer-frontend \
  --source ./frontend \
  --project <YOUR_PROJECT_ID> \
  --region us-central1 \
  --allow-unauthenticated
```

---

## 🔒 Security & Privacy
* **Zero Credential Exposure**: No API keys or OAuth secrets are bundled into client-side code.
* **Encrypted Sessions**: Session cookies are signed with cryptographically random state tokens.
* **Granular OAuth Scopes**: Scoped access restricted to read-only metadata and bill detection without full inbox modification rights.

---

## 📄 License
This project is open-source under the [Apache 2.0 License](LICENSE).
