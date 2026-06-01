import asyncio
import sys

sys.path.insert(0, "/home/ambarish/analytics-copilot")

from backend.agent.nodes.intent import understand_intent
from backend.agent.nodes.supervisor import supervisor
from backend.agent.nodes.responder import compose_response

async def test_schema_info_flow():
    print("Testing 'schema_info' routing and response...")
    print("=" * 60)
    
    # Simulating the question from the screenshot
    question = "Analyze columns inside reliance_unified"
    state = {
        "user_question": question,
        "datasource_id": "limese",
        "conversation_history": [],
        "session_id": "test_session",
        "conversation_id": "test_conv",
        "user_id": "test_user",
        "step_errors": [],
        "query_results": {}
    }
    
    # 1. Intent Classification
    intent_state = await understand_intent(state)
    print(f"1. Intent Classified: {intent_state.get('intent', {})}")
    assert intent_state.get("intent", {}).get("type") == "schema_info", "Intent should be classified as schema_info"
    
    # 2. Supervisor Routing
    supervisor_state = await supervisor(intent_state)
    print(f"2. Supervisor Next Step: {supervisor_state.get('next_step')}")
    assert supervisor_state.get("next_step") == "compose_response", "Supervisor should route to compose_response"
    
    # 3. Response Generation
    final_state = await compose_response(supervisor_state)
    response_text = final_state.get("response_text", "")
    
    print("\n3. Generated Response Text Preview:")
    print("-" * 60)
    print(response_text[:800])
    print("-" * 60)
    
    print("\nFollow-up Suggestions:")
    print(final_state.get("follow_up_questions"))
    
    assert "Schema Analysis: `reliance_unified`" in response_text, "Should contain reliance_unified schema header"
    print("\n🎉 SCHEMA INFO INTEGRATION TESTS PASSED PERFECTLY!")

if __name__ == "__main__":
    asyncio.run(test_schema_info_flow())
