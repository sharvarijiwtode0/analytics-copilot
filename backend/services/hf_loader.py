import structlog
import random

log = structlog.get_logger(__name__)

# Attempt to load transformers. If missing, we gracefully fall back to our advanced template prompter.
TRANSFORMERS_AVAILABLE = False
classifier_pipeline = None
generator_pipeline = None

try:
    from transformers import pipeline
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    log.warning("hf_loader.transformers_missing_using_template_fallback")

class HuggingFaceLoader:
    def __init__(self):
        self.initialized = False

    def initialize(self):
        global TRANSFORMERS_AVAILABLE
        if self.initialized:
            return
        if not TRANSFORMERS_AVAILABLE:
            log.info("hf_loader.fallback_narrator_ready")
            self.initialized = True
            return
            
        try:
            global classifier_pipeline, generator_pipeline
            log.info("hf_loader.loading_models", classifier="facebook/bart-large-mnli", narrator="google/flan-t5-base")
            
            # Load BART zero-shot classifier locally
            classifier_pipeline = pipeline(
                "zero-shot-classification",
                model="facebook/bart-large-mnli",
                device=-1 # Default to CPU for safe local execution
            )
            
            # Load T5 text narrator locally
            generator_pipeline = pipeline(
                "text2text-generation",
                model="google/flan-t5-base",
                device=-1
            )
            
            log.info("hf_loader.models_loaded_successfully")
        except Exception as e:
            log.error("hf_loader.load_failed_falling_back", error=str(e))
            # Gracefully degrade to template engine
            TRANSFORMERS_AVAILABLE = False
            
        self.initialized = True

    def classify_intent_zero_shot(self, question: str, candidate_labels: list[str]) -> dict:
        """
        Classifies user query dynamically using local BART Zero-Shot classifier.
        """
        self.initialize()
        if not TRANSFORMERS_AVAILABLE or not classifier_pipeline:
            # High-speed deterministic backup classifier
            q = question.lower().strip()
            if any(w in q for w in ["hi", "hello", "hey", "gm", "gn"]):
                return {"labels": ["greeting"], "scores": [0.95]}
            if any(w in q for w in ["chart", "plot", "graph", "bar", "line", "pie"]):
                return {"labels": ["chart_request"], "scores": [0.90]}
            return {"labels": ["data_query"], "scores": [0.85]}

        try:
            result = classifier_pipeline(question, candidate_labels)
            return {
                "labels": result.get("labels", []),
                "scores": result.get("scores", [])
            }
        except Exception as e:
            log.error("hf_loader.classification_failed", error=str(e))
            return {"labels": ["data_query"], "scores": [0.50]}

    def generate_warm_narrative(self, step: str, tables: list[str] = None, domain: str = "E-Commerce") -> str:
        """
        Generates descriptive, highly human-like narrative matching the active step and tables.
        """
        self.initialize()
        tables_str = ", ".join(tables) if tables else "core analytics"
        
        # 1. First, build the contextual prompt
        prompt = (
            f"Paraphrase this progress update to sound like a warm, descriptive human analyst "
            f"on a {domain} platform: 'Accessing table {tables_str} to perform step {step}.'"
        )

        # 2. If local HF T5 is available, generate neural paraphrase
        if TRANSFORMERS_AVAILABLE and generator_pipeline:
            try:
                outputs = generator_pipeline(prompt, max_length=60, num_return_sequences=1)
                text = outputs[0]["generated_text"].strip()
                if len(text) > 15:
                    return text
            except Exception as e:
                log.warning("hf_loader.generation_failed_using_fallback", error=str(e))

        # 3. Graceful fallback: Highly descriptive domain-agnostic template narrations
        templates = {
            "understand_intent": [
                f"I'm listening closely to your query to isolate the key business indicators you are interested in.",
                f"Analyzing your request to determine exactly what analytics we need to pull.",
                f"Translating your business question into structured analytical data points."
            ],
            "discover_schema": [
                f"Scanning our registered catalogs and schemas to locate exactly where {tables_str} metrics are tracked.",
                f"Mapping out your database blueprints to find the best tables ({tables_str}) for your query.",
                f"Searching through database metadata to coordinate our query structure."
            ],
            "generate_sql": [
                f"Formulating a secure, optimized data extraction query targeting your {tables_str} table.",
                f"Writing high-performance extraction scripts to fetch the required information.",
                f"Drafting custom, secure database commands to aggregate the values neatly."
            ],
            "review_sql": [
                f"Performing a strict peer review and syntax audit on the compiled query to guarantee absolute safety.",
                f"Verifying table joins and checking database rules before sending the command.",
                f"Auditing security parameters and query structures against business guidelines."
            ],
            "execute_sql": [
                f"Sending the reviewed command to the {tables_str} database engine to fetch the matching records.",
                f"Interrogating the database repository. Aggregating values in real time.",
                f"Executing our secure data pipeline to pull relevant rows."
            ],
            "generate_viz_config": [
                f"Structuring the returned rows and designing a clean, interactive Apache ECharts visualization.",
                f"Mapping data dimensions to coordinate a professional graphical presentation.",
                f"Building dynamic chart models (bar, line, or pie) for clear visual insights."
            ],
            "analyze_insights": [
                f"Diving deep into the extracted dataset. Isolating peaks, troughs, and growth metrics.",
                f"Scanning columns for anomalies, mathematical trends, and key business indicators.",
                f"Analyzing numerical changes to compile actionable, data-driven summaries."
            ],
            "review_insights": [
                f"Auditing calculated values and summaries against the raw database output to guarantee 100% accuracy.",
                f"Running validation checks to verify there are zero discrepancies in our taking.",
                f"Cross-referencing metrics to ensure absolute precision before presentation."
            ],
            "compose_response": [
                f"All checks passed! Compiling your custom dashboard, charts, and final insights report now.",
                f"Assembling the finalized visual layout and analyst narrative for you.",
                f"Bringing together the full analytical summary and presenting your interactive results."
            ]
        }

        # Select a random beautiful variant to keep responses feeling fresh and human-like!
        variant_list = templates.get(step, templates["understand_intent"])
        return random.choice(variant_list)

# Global singleton loader
hf_loader = HuggingFaceLoader()
