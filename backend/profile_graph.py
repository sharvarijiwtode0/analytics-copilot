import asyncio
import time
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.nodes.intent import understand_intent
from backend.agent.nodes.schema import discover_schema
from backend.agent.nodes.sql_gen import generate_sql
from backend.agent.nodes.executor import execute_sql
from backend.agent.nodes.analyst import analyze_insights
from backend.agent.nodes.viz_config import generate_viz_config
from backend.agent.nodes.responder import compose_response

log = structlog.get_logger()

async def profile():
    print("=== PROFILING PIPELINE ===")
    state: AnalyticsState = {
        "session_id": "test_profile_session",
        "conversation_id": "test_profile_conv",
        "user_question": "What is the total number of items in the combined_sales_final table?",
        "datasource_id": "limese",
        "conversation_history": [],
        "user_id": "anonymous",
        "step_errors": [],
    }

    steps = [
        ("intent", understand_intent),
        ("schema", discover_schema),
        ("sql_gen", generate_sql),
        ("executor", execute_sql),
        ("analyst", analyze_insights),
        ("viz_config", generate_viz_config),
        ("responder", compose_response),
    ]

    for name, node in steps:
        t0 = time.perf_counter()
        print(f"Starting step: {name}...")
        try:
            state = await node(state)
            duration = time.perf_counter() - t0
            print(f"Finished step: {name} in {duration:.2f} seconds")
            if "error" in state and state["error"]:
                print(f"⚠️ Error in state after {name}: {state['error']}")
        except Exception as e:
            print(f"❌ Exception in step {name}: {e}")
            break

if __name__ == "__main__":
    asyncio.run(profile())
