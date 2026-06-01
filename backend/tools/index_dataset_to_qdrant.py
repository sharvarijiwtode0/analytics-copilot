import json
import os
import sys
import structlog

log = structlog.get_logger(__name__)

# Ensure python path finds backend
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.training_collector import DATASET_PATH
from backend.agent.memory import vector_memory

def index_dataset():
    print("🌱 Indexing verified training dataset to Qdrant...")
    
    if not os.path.exists(DATASET_PATH):
        print(f"❌ Dataset file not found at: {DATASET_PATH}")
        return False
        
    # Connect to Qdrant
    vector_memory.connect()
    if not vector_memory.enabled or not vector_memory.client:
        print("❌ Qdrant vector memory is disabled or not running.")
        return False
        
    records = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                # Only index verified queries
                if record.get("status") == "verified":
                    records.append(record)
            except Exception as e:
                print(f"⚠️ Failed to parse line: {e}")
                
    total = len(records)
    print(f"📋 Found {total} verified queries to index.")
    
    indexed_count = 0
    for idx, r in enumerate(records):
        try:
            print(f"[{idx+1}/{total}] Indexing: '{r['question'][:50]}...'")
            vector_memory.store_query(
                user_id="anonymous",
                question=r["question"],
                sql=r["sql"],
                payload={
                    "datasource_id": "limese",
                    "confidence_score": r["confidence_score"]
                }
            )
            indexed_count += 1
        except Exception as e:
            print(f"❌ Failed to index record: {e}")
            
    print(f"\n✨ Successfully indexed {indexed_count} golden queries to Qdrant vector memory!")
    return True

if __name__ == "__main__":
    index_dataset()
