"""
Dynamic Schema Agent — Analytics Copilot
=======================================
Zero-latency utility that uses the pre-loaded in-memory database intelligence context
to perform dynamic schema expansion, column synonym matching, and automatic database 
error correction.
"""
from __future__ import annotations
import re
import structlog
from backend.services.db_intelligence import get_db_context

log = structlog.get_logger(__name__)

# Hardcoded common colloquial business synonym mappings to physical database columns
BUSINESS_SYNONYMS: dict[str, str] = {
    "volume": "quantity_ordered",
    "qty": "quantity_ordered",
    "quantity": "quantity_ordered",
    "sales": "row_subtotal",
    "revenue": "row_subtotal",
    "subtotal": "row_subtotal",
    "price": "row_subtotal",
    "spend": "row_subtotal",
    "cost": "cogs",
    "shipped": "quantity_ordered",
    "shipped_qty": "quantity_ordered",
    "stock": "inventory",
    "margin": "mrp - cogs",
}

class DynamicSchemaAgent:
    def __init__(self, datasource_id: str = "limese", schema: dict | None = None):
        self.datasource_id = datasource_id
        if datasource_id == "limese":
            try:
                self.context = get_db_context()
            except Exception as e:
                log.warning("dynamic_schema.failed_to_load_context", error=str(e))
                self.context = {"tables": {}}
        else:
            self.context = {"tables": {}}
            if schema and "tables" in schema:
                tables_dict = {}
                for t in schema["tables"]:
                    tables_dict[t["name"]] = {
                        "name": t["name"],
                        "columns": t.get("columns", []),
                        "aliases": [],
                        "row_count": t.get("row_count", 0),
                        "description": t.get("description", ""),
                    }
                self.context["tables"] = tables_dict

    def match_synonym_or_column(self, term: str) -> dict[str, str] | None:
        """
        Check if a term matches a business synonym or matches a column name directly.
        Returns a dict with table and column details.
        """
        t_clean = term.lower().strip().replace("'", "").replace("`", "")
        if not t_clean:
            return None

        # 1. Check exact business synonyms first
        if t_clean in BUSINESS_SYNONYMS:
            target_col = BUSINESS_SYNONYMS[t_clean]
            # Find which table holds this column
            tables = self.context.get("tables", {})
            for tname, tdata in tables.items():
                for col in tdata.get("columns", []):
                    if col["name"] == target_col:
                        return {
                            "table": tname,
                            "column": target_col,
                            "annotation": col.get("annotation", ""),
                            "matched_via": f"synonym '{t_clean}'",
                        }

        # 2. Check direct table/column names in the cached schema
        tables = self.context.get("tables", {})
        for tname, tdata in tables.items():
            for col in tdata.get("columns", []):
                col_name = col["name"].lower()
                if t_clean == col_name or t_clean in col_name:
                    return {
                        "table": tname,
                        "column": col["name"],
                        "annotation": col.get("annotation", ""),
                        "matched_via": "partial match",
                    }

        return None

    def scan_question_for_missing_tables(self, question: str, current_tables: list[str], allowed_tables: list[str] | None = None) -> list[str]:
        """
        Scan the user's question for any terms matching table names or their aliases/synonyms
        that weren't originally discovered, and return those table names to expand schema.
        """
        q_lower = question.lower()
        tables = self.context.get("tables", {})
        new_tables = list(current_tables)

        for tname, tdata in tables.items():
            if tname in new_tables:
                continue
            if allowed_tables is not None and tname not in allowed_tables:
                continue

            # Check if table name is in question
            t_clean = tname.lower().replace("_", " ").replace("-", " ")
            if tname.lower() in q_lower or t_clean in q_lower:
                new_tables.append(tname)
                continue

            # Check aliases/keywords in table metadata
            for alias in tdata.get("aliases", []):
                if alias.lower() in q_lower:
                    new_tables.append(tname)
                    break

        return new_tables[:4]  # cap at 4 tables max to prevent prompt clutter

    def resolve_execution_error(self, error_msg: str, sql_query: str) -> str | None:
        """
        Parse ClickHouse/SQL execution error, identify missing columns/tables,
        and generate actionable correction advice.
        """
        err_lower = error_msg.lower()
        
        # 1. Parse missing table error
        # e.g., "Table limese.combined_sales does not exist" or "table ... not found"
        table_match = re.search(r"table\s+['`\"]?([a-zA-Z0-9_\-\.]+?)['`\"]?\s+does\s+not\s+exist", err_lower)
        if not table_match:
            table_match = re.search(r"table\s+['`\"]?([a-zA-Z0-9_\-\.]+?)['`\"]?\s+not\s+found", err_lower)

        if table_match:
            wrong_table = table_match.group(1).split(".")[-1]  # get last part (strip schema prefix)
            # Find closest table match
            tables = self.context.get("tables", {})
            best_match = None
            for tname in tables.keys():
                if wrong_table in tname or tname in wrong_table:
                    best_match = tname
                    break
            
            if best_match:
                return f"Table '{wrong_table}' does not exist. Did you mean the table '{best_match}'?"
            return f"Table '{wrong_table}' does not exist. Please check the schema context for correct table names."

        # 2. Parse missing column error
        # e.g., "unknown identifier: volume" or "missing columns: 'shipped_qty'" or "no such column: margin"
        col_patterns = [
            r"unknown\s+identifier:\s*['`\"]?([a-zA-Z0-9_]+?)['`\"]?(?:\s|$)",
            r"missing\s+columns:\s*['`\"]?([a-zA-Z0-9_]+?)['`\"]?",
            r"no\s+such\s+column:\s*['`\"]?([a-zA-Z0-9_]+?)['`\"]?",
            r"column\s+['`\"]?([a-zA-Z0-9_]+?)['`\"]?\s+does\s+not\s+exist",
        ]
        
        wrong_col = None
        for pattern in col_patterns:
            m = re.search(pattern, err_lower)
            if m:
                wrong_col = m.group(1)
                break

        if wrong_col:
            # Let's try matching this column to our synonyms or database columns
            match_res = self.match_synonym_or_column(wrong_col)
            if match_res:
                return (
                    f"Column '{wrong_col}' is incorrect. "
                    f"Did you mean column '{match_res['column']}' in table '{match_res['table']}'? "
                    f"(Annotation: {match_res['annotation']})"
                )
            
            # Find if the column exists in any other table in our cache
            tables = self.context.get("tables", {})
            suggestions = []
            for tname, tdata in tables.items():
                for col in tdata.get("columns", []):
                    cname = col["name"]
                    # Calculate basic similarity or simple substring match
                    if wrong_col in cname or cname in wrong_col:
                        suggestions.append(f"'{cname}' in '{tname}'")
            
            if suggestions:
                return f"Column '{wrong_col}' is incorrect. Suggestions: {', '.join(suggestions[:3])}."
            return f"Column '{wrong_col}' does not exist in the referenced tables. Check the schema definition."

        return None
