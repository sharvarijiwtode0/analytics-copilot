#!/bin/bash
# Data Visualization Copilot — Start Script
set -e

echo "🚀 Starting Data Visualization Copilot..."
cd "$(dirname "$0")"

# Create .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Created .env from template — add your GROQ_API_KEY"
fi

# 1. Setup Virtual Environment
if [ ! -d "venv" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv venv
fi

echo "🔌 Activating virtual environment..."
source venv/bin/activate

# 2. Install Python deps (only if requirements.txt changed since last install)
REQS_HASH_FILE=".reqs_hash"
CURRENT_HASH=$(md5sum requirements.txt | awk '{print $1}')
if [ ! -f "$REQS_HASH_FILE" ] || [ "$(cat $REQS_HASH_FILE)" != "$CURRENT_HASH" ]; then
  echo "📥 Installing/Updating dependencies..."
  pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple qdrant-client fastembed redis
  echo "$CURRENT_HASH" > "$REQS_HASH_FILE"
else
  echo "✅ Dependencies up to date (skipping reinstall)"
fi

# 3. Start Docker services (Redis + Qdrant) if not already running
if command -v docker &> /dev/null; then
  echo "🐳 Ensuring Redis, Qdrant & PostgreSQL are running..."
  # Try docker compose up (non-fatal — services may already be running)
  sudo docker compose --profile vector up -d redis qdrant postgres 2>/dev/null || \
  sudo docker compose up -d redis qdrant postgres 2>/dev/null || \
  docker compose up -d redis qdrant postgres 2>/dev/null || \
  echo "⚠️  Could not start Docker services (may already be running or not installed)"

  # Wait for Redis to be ready (up to 15 seconds)
  REDIS_PORT=$(grep REDIS_URL .env 2>/dev/null | grep -oP ':\K[0-9]+(?=/)' | tail -1 || echo "6380")
  echo "⏳ Waiting for Redis on port $REDIS_PORT..."
  for i in $(seq 1 15); do
    if nc -z localhost "$REDIS_PORT" 2>/dev/null; then
      echo "✅ Redis is ready"
      break
    fi
    sleep 1
  done

  # Wait for Qdrant to be ready (up to 15 seconds)
  echo "⏳ Waiting for Qdrant on port 6333..."
  for i in $(seq 1 15); do
    if curl -sf http://localhost:6333/healthz > /dev/null 2>&1; then
      echo "✅ Qdrant is ready"
      break
    fi
    sleep 1
  done
else
  echo "⚠️  Docker not found — skipping Redis/Qdrant startup"
fi

# 4. Build frontend if not already built
if [ ! -f backend/static/index.html ]; then
  echo "⚙️  Building frontend (first time)..."
  cd frontend && npm install --silent 2>/dev/null && npx vite build --silent 2>/dev/null && cd ..
  echo "✅ Frontend built"
fi

mkdir -p uploads

# 5. Pre-warm the fastembed embedding model so first user query is instant
echo "🔥 Pre-warming embedding model..."
python -c "
from fastembed import TextEmbedding
try:
    m = TextEmbedding('BAAI/bge-small-en-v1.5')
    list(m.embed(['warmup']))
    print('  ✅ Embedding model ready')
except Exception as e:
    print(f'  ⚠️  Embedding model warmup failed (non-fatal): {e}')
" 2>/dev/null || echo "  ⚠️  Embedding model warmup skipped"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Data Visualization Copilot"
echo "  Open in browser → http://localhost:8001"
echo "  API Docs        → http://localhost:8001/docs"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

export PYTHONPATH=$PYTHONPATH:.
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload
