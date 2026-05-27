import asyncio
import json
from backend.agent.llm import call_llm

async def test():
    prompt = """You are the Supervisor/Planner.
Decompose this request: "can you show me revenue by platform?"

Return ONLY a JSON list of subtasks in this exact format:
[
  {"task": "Retrieve sales data", "assigned_to": "sql_data"},
  {"task": "Aggregate sales by platform", "assigned_to": "sql_data"}
]"""
    
    print("Sending prompt to GLM-5-Turbo...")
    res = await call_llm([{"role": "user", "content": prompt}], task="routing")
    print("\n--- Raw GLM Output ---")
    print(res.content)
    print("----------------------")
    
    try:
        data = json.loads(res.content.strip())
        print("✅ Parse successful!")
        print(data)
    except Exception as e:
        print(f"❌ Parse failed: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test())
