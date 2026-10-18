"""Send one tiny request to the provider set in .env and print what comes back.

    python scripts/check_llm.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control_tower.agent import PROVIDERS, default_model, provider  # noqa: E402
from control_tower.cli import load_dotenv  # noqa: E402

load_dotenv()
name = provider()
key_var, default, _ = PROVIDERS[name]
print(f"provider: {name}, model: {os.environ.get('LLM_MODEL') or default}, "
      f"{key_var}: {len(os.environ.get(key_var, ''))} characters")
try:
    reply = default_model().invoke("Réponds juste : OK")
    print("OK, the model answered:", reply.text)
except Exception as e:  # noqa: BLE001
    print("FAILED:", type(e).__name__, str(e)[:800])
