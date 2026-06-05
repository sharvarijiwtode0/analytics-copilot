import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class FakeLLMResponse:
    content: str
    model: str = "test-model"
    input_tokens: int = 10
    output_tokens: int = 20
    latency_ms: int = 5
