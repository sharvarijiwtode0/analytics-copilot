import json
import structlog
from pathlib import Path
from typing import Any
from qdrant_client.http import models
from backend.config import settings
from backend.agent.memory import VectorMemory

log = structlog.get_logger(__name__)

# Collection names
BUSINESS_KNOWLEDGE_COLLECTION = "business_knowledge"
DB_KNOWLEDGE_COLLECTION = "db_knowledge"
QA_MEMORY_COLLECTION = "qa_memory"


class BusinessKnowledgeService:
    """Service for managing business knowledge in Qdrant."""

    def __init__(self, vector_memory: VectorMemory):
        self.vector_memory = vector_memory
        self.knowledge_path = Path(__file__).parent.parent.parent / "data" / "business_knowledge.json"

    def ensure_collection(self):
        """Ensure the business knowledge collection exists in Qdrant."""
        if not self.vector_memory.enabled:
            return

        try:
            collections = self.vector_memory.client.get_collections().collections
            if not any(c.name == BUSINESS_KNOWLEDGE_COLLECTION for c in collections):
                self.vector_memory.client.create_collection(
                    collection_name=BUSINESS_KNOWLEDGE_COLLECTION,
                    vectors_config=models.VectorParams(
                        size=768,  # BAAI/bge-base-en-v1.5 dim
                        distance=models.Distance.COSINE
                    )
                )
                log.info("business_knowledge.collection_created")
        except Exception as e:
            log.error("business_knowledge.collection_create_failed", error=str(e))

    def load_knowledge_base(self) -> dict[str, Any]:
        """Load business knowledge from JSON file."""
        try:
            with open(self.knowledge_path) as f:
                return json.load(f)
        except FileNotFoundError:
            log.warning("business_knowledge.file_not_found", path=str(self.knowledge_path))
            return {"kpi_definitions": [], "ambiguous_keywords": [], "business_rules": [], "glossary": []}
        except json.JSONDecodeError as e:
            log.error("business_knowledge.invalid_json", error=str(e))
            return {"kpi_definitions": [], "ambiguous_keywords": [], "business_rules": [], "glossary": []}

    def index_knowledge(self):
        """Index business knowledge into Qdrant."""
        if not self.vector_memory.enabled:
            return

        self.ensure_collection()
        knowledge = self.load_knowledge_base()

        points = []
        point_id = 0

        # Index KPI definitions
        for kpi in knowledge.get("kpi_definitions", []):
            text = f"{kpi['name']}: {kpi.get('definition', '')} {kpi.get('calculation', '')} {kpi.get('description', '')}"
            vector = self.vector_memory.embed_text(text)
            if vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "type": "kpi",
                        "name": kpi["name"],
                        "definition": kpi.get("definition", ""),
                        "calculation": kpi.get("calculation", ""),
                        "tables_used": kpi.get("tables_used", []),
                        "columns": kpi.get("columns", []),
                        "description": kpi.get("description", ""),
                        "text": text
                    }
                ))
                point_id += 1

        # Index ambiguous keywords
        for kw in knowledge.get("ambiguous_keywords", []):
            meanings_text = "; ".join(kw.get("meanings", kw.get("aliases", [])))
            text = f"{kw['keyword']}: {meanings_text} {kw.get('description', '')}"
            vector = self.vector_memory.embed_text(text)
            if vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "type": "ambiguous_keyword",
                        "keyword": kw["keyword"],
                        "meanings": kw.get("meanings", kw.get("aliases", [])),
                        "context_clues": kw.get("context_clues", []),
                        "tables": kw.get("tables", []),
                        "description": kw.get("description", ""),
                        "text": text
                    }
                ))
                point_id += 1

        # Index business rules
        for rule in knowledge.get("business_rules", []):
            text = f"Rule: {rule}"
            vector = self.vector_memory.embed_text(text)
            if vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "type": "business_rule",
                        "rule": rule,
                        "text": text
                    }
                ))
                point_id += 1

        # Index glossary
        for term in knowledge.get("glossary", []):
            text = f"{term['term']}: {term['definition']}"
            vector = self.vector_memory.embed_text(text)
            if vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "type": "glossary",
                        "term": term["term"],
                        "definition": term["definition"],
                        "text": text
                    }
                ))
                point_id += 1

        # Index industry context
        if knowledge.get("industry_context"):
            text = f"Industry context: {knowledge['industry_context']}"
            vector = self.vector_memory.embed_text(text)
            if vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "type": "industry_context",
                        "context": knowledge["industry_context"],
                        "text": text
                    }
                ))
                point_id += 1

        # Bulk upsert
        if points:
            self.vector_memory.client.upsert(
                collection_name=BUSINESS_KNOWLEDGE_COLLECTION,
                points=points
            )
            log.info("business_knowledge.indexed", count=len(points))

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search business knowledge for relevant context."""
        if not self.vector_memory.enabled:
            return []

        try:
            vector = self.vector_memory.embed_text(query)
            results = self.vector_memory.client.query_points(
                collection_name=BUSINESS_KNOWLEDGE_COLLECTION,
                query=vector,
                limit=limit,
                score_threshold=0.7
            )
            return [res.payload for res in results.points]
        except Exception as e:
            log.error("business_knowledge.search_failed", error=str(e))
            return []

    def check_ambiguous_keywords(self, query: str) -> dict[str, Any] | None:
        """Check if query contains ambiguous keywords."""
        if not self.vector_memory.enabled:
            return None

        knowledge = self.load_knowledge_base()
        query_lower = query.lower()

        for kw in knowledge.get("ambiguous_keywords", []):
            keyword = kw["keyword"].lower()
            if keyword in query_lower:
                meanings = kw.get("meanings", [])
                if len(meanings) > 1:
                    return {
                        "keyword": kw["keyword"],
                        "meanings": meanings,
                        "context_clues": kw.get("context_clues", [])
                    }
        return None

    def get_kpi_definition(self, kpi_name: str) -> dict[str, Any] | None:
        """Get KPI definition by name."""
        knowledge = self.load_knowledge_base()
        for kpi in knowledge.get("kpi_definitions", []):
            if kpi["name"].lower() == kpi_name.lower():
                return kpi
        return None

    def get_business_rules(self) -> list[str]:
        """Get all business rules."""
        knowledge = self.load_knowledge_base()
        return knowledge.get("business_rules", [])


class DBKnowledgeService:
    """Service for managing database schema knowledge in Qdrant."""

    def __init__(self, vector_memory: VectorMemory):
        self.vector_memory = vector_memory

    def ensure_collection(self):
        """Ensure the DB knowledge collection exists in Qdrant."""
        if not self.vector_memory.enabled:
            return

        try:
            collections = self.vector_memory.client.get_collections().collections
            if not any(c.name == DB_KNOWLEDGE_COLLECTION for c in collections):
                self.vector_memory.client.create_collection(
                    collection_name=DB_KNOWLEDGE_COLLECTION,
                    vectors_config=models.VectorParams(
                        size=768,
                        distance=models.Distance.COSINE
                    )
                )
                log.info("db_knowledge.collection_created")
        except Exception as e:
            log.error("db_knowledge.collection_create_failed", error=str(e))

    def index_table_schema(self, table_name: str, schema_info: dict[str, Any]):
        """Index a table's schema into Qdrant."""
        if not self.vector_memory.enabled:
            return

        self.ensure_collection()
        points = []
        point_id = hash(table_name) % ((1 << 31) - 1)

        # Table-level chunk
        table_text = (
            f"Table: {table_name}. "
            f"Description: {schema_info.get('description', '')}. "
            f"Row count: {schema_info.get('row_count', 0)}. "
            f"Columns: {', '.join(c.get('name', '') for c in schema_info.get('columns', [])[:10])}."
        )
        table_vector = self.vector_memory.embed_text(table_text)
        if table_vector:
            points.append(models.PointStruct(
                id=point_id,
                vector=table_vector,
                payload={
                    "type": "table",
                    "table_name": table_name,
                    "aliases": schema_info.get("aliases", []),
                    "row_count": schema_info.get("row_count", 0),
                    "description": schema_info.get("description", ""),
                    "text": table_text
                }
            ))
            point_id += 1

        # Column-level chunks
        for col in schema_info.get("columns", []):
            col_text = (
                f"Column: {col['name']} in table {table_name}. "
                f"Type: {col.get('type', '')}. "
                f"Description: {col.get('description', '')}. "
                f"Sample values: {', '.join(str(v) for v in col.get('sample_values', [])[:5])}."
            )
            col_vector = self.vector_memory.embed_text(col_text)
            if col_vector:
                points.append(models.PointStruct(
                    id=point_id,
                    vector=col_vector,
                    payload={
                        "type": "column",
                        "table_name": table_name,
                        "column_name": col["name"],
                        "column_type": col.get("type", ""),
                        "description": col.get("description", ""),
                        "sample_values": col.get("sample_values", []),
                        "is_categorical": col.get("is_categorical", False),
                        "text": col_text
                    }
                ))
                point_id += 1

        if points:
            self.vector_memory.client.upsert(
                collection_name=DB_KNOWLEDGE_COLLECTION,
                points=points
            )
            log.info("db_knowledge.table_indexed", table=table_name, chunks=len(points))

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search DB knowledge for relevant tables/columns."""
        if not self.vector_memory.enabled:
            return []

        try:
            vector = self.vector_memory.embed_text(query)
            results = self.vector_memory.client.query_points(
                collection_name=DB_KNOWLEDGE_COLLECTION,
                query=vector,
                limit=limit,
                score_threshold=0.65
            )
            return [res.payload for res in results.points]
        except Exception as e:
            log.error("db_knowledge.search_failed", error=str(e))
            return []

    def resolve_table_name(self, query: str) -> list[str]:
        """Resolve table names from natural language query."""
        results = self.search(query, limit=10)
        tables = set()
        for r in results:
            if r.get("type") == "table":
                tables.add(r["table_name"])
                for alias in r.get("aliases", []):
                    if alias.lower() in query.lower():
                        tables.add(r["table_name"])
        return list(tables)


class QAMemoryService:
    """Service for storing and retrieving Q&A pairs."""

    def __init__(self, vector_memory: VectorMemory):
        self.vector_memory = vector_memory

    def ensure_collection(self):
        """Ensure the QA memory collection exists in Qdrant."""
        if not self.vector_memory.enabled:
            return

        try:
            collections = self.vector_memory.client.get_collections().collections
            if not any(c.name == QA_MEMORY_COLLECTION for c in collections):
                self.vector_memory.client.create_collection(
                    collection_name=QA_MEMORY_COLLECTION,
                    vectors_config=models.VectorParams(
                        size=768,
                        distance=models.Distance.COSINE
                    )
                )
                log.info("qa_memory.collection_created")
        except Exception as e:
            log.error("qa_memory.collection_create_failed", error=str(e))

    def store_qa(
        self,
        question: str,
        answer: str,
        sql: str,
        tables: list[str],
        columns: list[str],
        user_id: str = "anonymous",
        viz_type: str | None = None
    ):
        """Store a Q&A pair."""
        if not self.vector_memory.enabled:
            return

        self.ensure_collection()

        vector = self.vector_memory.embed_text(question)
        if not vector:
            return

        point_id = hash(question) % ((1 << 63) - 1)

        try:
            self.vector_memory.client.upsert(
                collection_name=QA_MEMORY_COLLECTION,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "question": question,
                            "answer": answer,
                            "sql": sql,
                            "tables": tables,
                            "columns": columns,
                            "viz_type": viz_type,
                            "user_id": user_id,
                            "timestamp": str(structlog.time.now())
                        }
                    )
                ]
            )
            log.info("qa_memory.stored", question=question[:50], user_id=user_id)
        except Exception as e:
            log.error("qa_memory.store_failed", error=str(e))

    def search(self, question: str, user_id: str = "anonymous", threshold: float = 0.92) -> dict[str, Any] | None:
        """Search for similar questions."""
        if not self.vector_memory.enabled:
            return None

        try:
            vector = self.vector_memory.embed_text(question)
            results = self.vector_memory.client.query_points(
                collection_name=QA_MEMORY_COLLECTION,
                query=vector,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id)
                        )
                    ]
                ),
                limit=1,
                score_threshold=threshold
            )
            if results.points:
                return results.points[0].payload
            return None
        except Exception as e:
            log.error("qa_memory.search_failed", error=str(e))
            return None


# Singleton instances
def get_business_knowledge_service() -> BusinessKnowledgeService:
    from backend.agent.memory import vector_memory
    return BusinessKnowledgeService(vector_memory)


def get_db_knowledge_service() -> DBKnowledgeService:
    from backend.agent.memory import vector_memory
    return DBKnowledgeService(vector_memory)


def get_qa_memory_service() -> QAMemoryService:
    from backend.agent.memory import vector_memory
    return QAMemoryService(vector_memory)
