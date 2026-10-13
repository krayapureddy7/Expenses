# My agent: expense-analyzer
One-liner: A conversational agent that helps users track expenses, plan budgets, set savings goals, discover budget-conscious dining & outings, and parse transactions with a catalog of expense records and recommendation cards.

Tool coverage:
- Memory: Vertex AI Memory Bank (`agentengine://8152861402650902528`) remembering user monthly budget, savings targets, dietary/dining preferences, home location, travel style, and past spending habits across sessions via `PreloadMemoryTool` and `generate_memories_callback`.
- Tools:
  - add_expense: Log individual expenses with amount, category, description, and date (persisted to Firestore).
  - batch_import_transactions: Parse and import multiple expenses (e.g. from pasted CSV/text or receipt dumps).
  - parse_payment_notification: Extract expense amount, merchant, and category from receipt/notification emails.
  - list_expenses: Query recorded expenses from Firestore and session with category filters.
  - analyze_budget: Calculate total spent, category percentages, budget burn rate, and projected end-of-month savings.
  - set_budget_and_goals: Set or adjust monthly spending limit and savings target.
  - geocode_address: Turn addresses/landmarks into coordinates using Google Maps Geocoding API REST endpoint.
  - search_nearby_places: Query nearby places of a specific type using Google Maps Places API (New) REST endpoint.
  - search_local_places: Query live real-world restaurants, cafes, and attractions via OpenStreetMap Nominatim API.
  - recommend_dining: Recommend restaurants, cafés, and lunch spots matching location, preferences, and available dining budget.
  - plan_outing: Plan weekend trips or day outings with cost breakdown (transport, food, stay, activities) and suggest budget-saving alternatives.
  - convert_currency: Real-time foreign currency conversions via Open Exchange Rates public API (reads EXCHANGE_RATE_API_KEY if configured).
  - export_expenses_to_gcs: Generate structured CSV reports from Firestore and upload to Cloud Storage with public download links.
  - clear_expenses: Reset session expense items.
- Catalog/UI: A2UI v0.8 Basic Catalog with `a2ui_callback` rendering rich cards (Cards, Columns, Rows, Texts, Images) in ADK Web.
- Image gen: Visual preview cards for outings/places, or visual spending milestone badges.
- Sandbox: Accurate financial calculations, amortizations, and projection math.

Recommended for every project: memory, storage, tools, image generation, A2UI
Agent-specific / stretch: CSV upload/parsing, receipt parsing simulation, outing cost optimizer, live currency rates API, Cloud Storage exporter, Google Maps Geocoding & Places (New)
