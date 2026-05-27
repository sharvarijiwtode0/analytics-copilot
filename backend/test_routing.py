"""
Dynamic Verification Script.
Tests semantic table routing and interactive clarification loops.
"""
import asyncio
import sys
from backend.agent.graph import run_analytics_agent

async def main():
    print("--- TEST 1: Vague Question (Should Trigger Clarification) ---")
    vague_q = "show me sales"
    res1 = await run_analytics_agent(
        question=vague_q,
        datasource_id="limese",
        session_id="verification_session_123",
        conversation_id="verification_conv_123",
        conversation_history=[]
    )
    
    print("\n[RESULT 1 - TEXT RESPONSE]:")
    print(res1.get("text"))
    print("\n[RESULT 1 - SUGGESTIONS (Chips):]")
    print(res1.get("follow_up_questions"))
    
    # Assert that clarification suggestions are generated
    assert len(res1.get("follow_up_questions", [])) > 0, "No clarification chips generated!"
    print("\n✅ Test 1 Passed: Vague question successfully triggered the clarification loop!")

    print("\n--- TEST 2: Clarification Click (Should Lock Table & Execute) ---")
    # Simulate user clicking a suggestion chip
    click_q = "Show Shopify Storefront Sales"
    res2 = await run_analytics_agent(
        question=click_q,
        datasource_id="limese",
        session_id="verification_session_123",
        conversation_id="verification_conv_123",
        conversation_history=[
            {"role": "user", "content": vague_q},
            {"role": "assistant", "content": res1.get("text")}
        ]
    )
    
    print("\n[RESULT 2 - GENERATED SQL]:")
    print(res2.get("sql"))
    print("\n[RESULT 2 - VIZ TYPE]:")
    print(res2.get("viz_type"))
    
    # Assert that it successfully resolved table selection and generated Shopify SQL
    sql_lower = res2.get("sql", "").lower()
    assert "shopify_orders" in sql_lower, f"Table shopify_orders not found in generated SQL: {res2.get('sql')}"
    print("\n✅ Test 2 Passed: Dynamic selection lock resolved Shopify table and generated target SQL successfully!")

if __name__ == "__main__":
    # Set PYTHONPATH
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    asyncio.run(main())
