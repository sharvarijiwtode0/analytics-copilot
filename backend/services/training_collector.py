import json
import os
import re
from datetime import datetime
import structlog

log = structlog.get_logger(__name__)

# Constants
DATASET_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "training_dataset.jsonl")


class TrainingCollectorService:
    def __init__(self, dataset_path: str = DATASET_PATH):
        self.dataset_path = dataset_path
        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.dataset_path), exist_ok=True)

    def _clean_sql(self, sql: str) -> str:
        """Strip SQL comments and formatting to keep dataset clean."""
        # Remove single-line comments
        sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
        # Remove multi-line comments
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        # Replace multiple spaces/newlines with single space
        sql = re.sub(r"\s+", " ", sql)
        return sql.strip()

    def evaluate_gates(self, question: str, sql: str, results: dict, was_fixed: bool) -> tuple[float, dict]:
        """
        Evaluate the 4 Quality Gates to assign a confidence score and validate the query pair.
        Returns (confidence_score, gate_results).
        """
        sql_lower = sql.lower()
        q_lower = question.lower()
        
        gate_results = {
            "gate1_grounding": False,
            "gate2_metric_alignment": True,  # Default True, set False if mismatch
            "gate3_constraint_match": True,  # Default True, set False if mismatch
            "gate4_clean_execution": not was_fixed
        }

        # --- GATE 1: Result Grounding (Data Presence) ---
        row_count = results.get("row_count", 0)
        if row_count > 0:
            gate_results["gate1_grounding"] = True

        # --- GATE 2: Metric Alignment ---
        # If user asks for revenue/sales, subtotal must be queried
        has_revenue_words = any(w in q_lower for w in ["sales", "revenue", "price", "subtotal", "spend", "earning"])
        if has_revenue_words and "row_subtotal" not in sql_lower:
            gate_results["gate2_metric_alignment"] = False
            
        # If user asks for units/qty, quantity_ordered must be queried
        has_unit_words = any(w in q_lower for w in ["units", "qty", "quantity", "volume", "shipped"])
        if has_unit_words and "quantity_ordered" not in sql_lower:
            gate_results["gate2_metric_alignment"] = False

        # --- GATE 3: Constraint Matching ---
        # Year check
        years = re.findall(r"\b(20[12]\d)\b", q_lower)
        for yr in years:
            if yr not in sql_lower:
                gate_results["gate3_constraint_match"] = False
                
        # Platforms / Channels check
        platforms = ["shopee", "tokopedia", "tiktok", "lazada", "shopify", "offline"]
        for pf in platforms:
            if pf in q_lower and pf not in sql_lower:
                gate_results["gate3_constraint_match"] = False

        # --- Calculate Confidence Score ---
        # Base score based on clean execution
        score = 1.0 if gate_results["gate4_clean_execution"] else 0.75
        
        # Deductions
        if not gate_results["gate1_grounding"]:
            score -= 0.3  # Serious penalty if query returned 0 rows
        if not gate_results["gate2_metric_alignment"]:
            score -= 0.2  # Penalty for metric mismatch
        if not gate_results["gate3_constraint_match"]:
            score -= 0.1  # Minor penalty for missing filter

        # Cap score between 0.0 and 1.0
        score = max(0.0, min(1.0, score))
        return round(score, 2), gate_results

    def collect_query(self, state: dict, sql: str, results: dict, was_fixed: bool = False) -> bool:
        """
        Main entry point to capture successful SQL executions.
        Validates, grades, and appends to the local JSONL dataset.
        """
        datasource_id = state.get("datasource_id")
        # Only collect for ClickHouse (Limese) datasource
        if datasource_id != "limese":
            return False

        question = state.get("intent", {}).get("rephrased_question") or state.get("user_question", "")
        if not question or not sql:
            return False

        try:
            clean_sql = self._clean_sql(sql)
            confidence, gates = self.evaluate_gates(question, clean_sql, results, was_fixed)

            # Skip queries that return no data and failed logic checks (very low quality)
            if confidence < 0.5:
                log.info("training_collector.skipped_low_quality", question=question[:60], score=confidence)
                return False

            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "question": question,
                "sql": clean_sql,
                "confidence_score": confidence,
                "gates": gates,
                "was_fixed": was_fixed,
                "row_count": results.get("row_count", 0),
                "execution_time_ms": results.get("execution_time_ms", 0),
                "status": "pending_review"  # Human must verify before model training
            }

            # Append to JSONL file
            with open(self.dataset_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            log.info("training_collector.success", question=question[:60], score=confidence)
            return True
        except Exception as e:
            log.error("training_collector.error", error=str(e))
            return False


training_collector = TrainingCollectorService()
