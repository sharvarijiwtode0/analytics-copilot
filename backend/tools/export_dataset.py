import json
import os
import random
import structlog

log = structlog.get_logger(__name__)

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
INPUT_FILE = os.path.join(DATA_DIR, "training_dataset.jsonl")
TRAIN_FILE = os.path.join(DATA_DIR, "train_dataset.jsonl")
EVAL_FILE = os.path.join(DATA_DIR, "eval_dataset.jsonl")

# Standard compact schema & rules for fine-tuning
SYSTEM_PROMPT = """You are a ClickHouse SQL expert. Given the database schema and rules, write a read-only SELECT query to answer the user's question.

=== DATABASE SCHEMA ===
Table: combined_sales_final (csf)
- date_created: DateTime
- row_subtotal: Decimal (representing: revenue, sales, total spend, value, amount, earnings, performance)
- quantity_ordered: Int (representing: units, qty, quantity, volume, count)
- final_status: String (order status: 'cancelled', 'returned', 'shipped', 'delivered', etc.)
- platform: String (sales channel: 'Shopee', 'Shopify', 'Tokopedia', 'offline', etc.)
- internal_sku: String
- order_id: String

Table: product_master (pm)
- internal_sku: String
- product_name: String
- category_l1: String (product category: 'Skincare', 'Makeup', 'Haircare')

=== CLICKHOUSE RULES ===
1. Table aliases are mandatory: write "FROM combined_sales_final csf" and "LEFT JOIN product_master pm ON csf.internal_sku = pm.internal_sku".
2. Exclude cancelled/returned orders: `csf.final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned')`.
3. Date filtering: csf.date_created >= '2025-01-01' (standard string comparison).
4. Date grouping: formatDateTime(csf.date_created, '%Y-%m') AS month, or '%Y-%m-%d' AS date for daily trends.
5. Revenue column: row_subtotal (NOT order_price).
6. Units column: quantity_ordered.
7. Always end with a LIMIT clause.

Respond ONLY with this JSON structure:
{
  "sql": "<complete ClickHouse SQL query>"
}"""


def export_dataset(split_ratio: float = 0.8):
    print("🚀 Starting Phase 2: ChatML Dataset Export & Formatting...")
    
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Input file not found: {INPUT_FILE}")
        return False

    records = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                # Only export verified records with confidence score >= 0.8
                if record.get("status") == "verified" and record.get("confidence_score", 0.0) >= 0.8:
                    records.append(record)
            except Exception as e:
                print(f"⚠️ Warning: failed to parse JSON line: {e}")

    total_records = len(records)
    print(f"📋 Found {total_records} verified high-quality records in training log.")
    
    if total_records < 5:
        print("❌ Cannot export: You need at least 5 verified records to split train/evaluation sets.")
        return False

    # Shuffle to ensure even distribution
    random.seed(42)
    random.shuffle(records)

    # Format into ChatML structure
    chatml_dataset = []
    for r in records:
        chatml_record = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": r["question"]},
                {"role": "assistant", "content": json.dumps({"sql": r["sql"]})}
            ]
        }
        chatml_dataset.append(chatml_record)

    # Split train and evaluation sets
    split_idx = int(total_records * split_ratio)
    train_data = chatml_dataset[:split_idx]
    eval_data = chatml_dataset[split_idx:]

    # Write training set
    with open(TRAIN_FILE, "w", encoding="utf-8") as f:
        for item in train_data:
            f.write(json.dumps(item) + "\n")

    # Write evaluation set
    with open(EVAL_FILE, "w", encoding="utf-8") as f:
        for item in eval_data:
            f.write(json.dumps(item) + "\n")

    print(f"\n✨ Phase 2 Export Complete!")
    print(f"  📂 Train Dataset:      {TRAIN_FILE} ({len(train_data)} records)")
    print(f"  📂 Evaluation Dataset: {EVAL_FILE} ({len(eval_data)} records)")
    return True


if __name__ == "__main__":
    export_dataset()
