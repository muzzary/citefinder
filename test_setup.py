from openai import OpenAI
from db import connect

# 1. Test Ollama via the OpenAI-compatible endpoint
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
r = client.chat.completions.create(
    model="phi4-mini",
    messages=[{"role": "user", "content": "Say 'LLM OK' and nothing else."}],
    temperature=0.0,
)
print("Ollama:", r.choices[0].message.content.strip())

# 2. Test Postgres + pgvector
with connect() as conn, conn.cursor() as cur:
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    print("pgvector version:", cur.fetchone())
print("Phase 0 complete.")