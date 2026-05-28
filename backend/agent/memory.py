import structlog
from qdrant_client import QdrantClient
from qdrant_client.http import models
from fastembed import TextEmbedding
from backend.config import settings

log = structlog.get_logger(__name__)

class VectorMemory:
    def __init__(self):
        self.enabled = settings.qdrant_enabled
        self.client = None
        self.embedding_model = None
        self.collection_name = settings.qdrant_collection

    def connect(self):
        if not self.enabled:
            return
        if not self.client:
            try:
                self.client = QdrantClient(url=settings.qdrant_url, timeout=5)
                self.embedding_model = TextEmbedding(model_name="BAAI/bge-base-en-v1.5")
                
                # Check if collection exists
                collections = self.client.get_collections().collections
                if not any(c.name == self.collection_name for c in collections):
                    self.client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=models.VectorParams(
                            size=768, # BAAI/bge-base-en-v1.5 dim
                            distance=models.Distance.COSINE
                        )
                    )
                log.info("qdrant.connected", url=settings.qdrant_url)
            except Exception as e:
                # Do NOT permanently disable — reset client so next call retries.
                # This handles cold-boot race conditions where Qdrant starts after the server.
                log.warning("qdrant.connection_failed_will_retry", error=str(e))
                self.client = None
                self.embedding_model = None

    def embed_text(self, text: str) -> list[float]:
        if not self.embedding_model:
            return []
        embeddings = list(self.embedding_model.embed([text]))
        return embeddings[0].tolist()

    def store_query(self, user_id: str, question: str, sql: str, payload: dict):
        if not self.client and self.enabled:
            self.connect()
        if not self.enabled:
            return

        try:
            vector = self.embed_text(question)
            payload["user_id"] = user_id
            payload["question"] = question
            payload["sql"] = sql
            
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=hash(question) % ((1<<63)-1), # simple ID
                        vector=vector,
                        payload=payload
                    )
                ]
            )
        except Exception as e:
            log.error("qdrant.store_failed", error=str(e))

    def search_similar_queries(self, question: str, user_id: str = "anonymous", limit: int = 3) -> list[dict]:
        if not self.client and self.enabled:
            self.connect()
        if not self.enabled:
            return []

        try:
            vector = self.embed_text(question)
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id)
                        )
                    ]
                ),
                limit=limit,
                score_threshold=0.85
            )
            return [res.payload for res in results.points]
        except Exception as e:
            log.error("qdrant.search_failed", error=str(e))
            return []

    def search_semantic_cache(self, question: str, user_id: str = "anonymous", threshold: float = 0.92) -> dict | None:
        if not self.client and self.enabled:
            self.connect()
        if not self.enabled:
            return None

        try:
            vector = self.embed_text(question)
            results = self.client.query_points(
                collection_name=self.collection_name,
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
                point = results.points[0]
                log.info("qdrant.semantic_cache_hit", score=point.score, question=question, matched=point.payload.get("question"))
                return point.payload
            return None
        except Exception as e:
            log.error("qdrant.semantic_cache_failed", error=str(e))
            return None

vector_memory = VectorMemory()
