"""Send one tiny request to Mistral and print what comes back.

Tells apart a bad key (401), an account without access or quota (429 on the
very first call), and a working key (200, with the rate-limit headers).

    python scripts/check_mistral.py
"""
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control_tower.cli import load_dotenv  # noqa: E402

load_dotenv()
key = os.environ.get("MISTRAL_API_KEY", "")
model = os.environ.get("MISTRAL_MODEL", "mistral-medium-latest")
print(f"key: {len(key)} characters, model: {model}")

r = httpx.post("https://api.mistral.ai/v1/chat/completions", timeout=30,
               headers={"Authorization": f"Bearer {key}"},
               json={"model": model, "max_tokens": 5, "messages": [{"role": "user", "content": "Bonjour"}]})
print("status:", r.status_code)
for h, v in r.headers.items():
    if "ratelimit" in h.lower() or h.lower() == "retry-after":
        print(f"  {h}: {v}")
print("body:", r.text[:800])
