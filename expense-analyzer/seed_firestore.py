#!/usr/bin/env python3
"""Seed initial sample budget data and expenses into Google Cloud Firestore."""

import datetime
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-04-d6542b445177"
COLLECTION_NAME = "expenses"

def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)

    sample_items = [
        {
            "id": "exp_seed_001",
            "date": "2026-10-10",
            "amount": 78.50,
            "category": "Groceries",
            "description": "Trader Joe's Weekly Groceries",
            "payment_method": "Credit Card",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "id": "exp_seed_002",
            "date": "2026-10-11",
            "amount": 16.25,
            "category": "Food & Dining",
            "description": "Chipotle Burrito Bowl & Drink",
            "payment_method": "Debit Card",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "id": "exp_seed_003",
            "date": "2026-10-12",
            "amount": 45.00,
            "category": "Travel & Commute",
            "description": "Clipper Card Transit Reload",
            "payment_method": "Apple Pay",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        {
            "id": "exp_seed_004",
            "date": "2026-10-12",
            "amount": 14.99,
            "category": "Entertainment",
            "description": "Monthly Streaming Subscription",
            "payment_method": "Credit Card",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    ]

    for item in sample_items:
        doc_ref = db.collection(COLLECTION_NAME).document(item["id"])
        doc_ref.set(item)
        print(f"Seeded document: {item['id']} -> ${item['amount']} ({item['description']})")

    print(f"Successfully seeded {len(sample_items)} documents into Firestore collection '{COLLECTION_NAME}'!")

if __name__ == "__main__":
    seed()
