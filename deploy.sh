#!/usr/bin/env bash
set -euo pipefail

# ═════════════════════════════════════════════════════════════════
#  MCP SearchTool v3.4 — Deploy Script
#  One-command setup: SearXNG + Python venv + модель + проверка
# ═════════════════════════════════════════════════════════════════

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/venv"
MODEL_DIR="$REPO_DIR/models"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  🚀 MCP SearchTool v3.4 — Deploy                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ──
echo "📋 Step 1/5: System dependencies..."
# Docker (for SearXNG)
if ! command -v docker &>/dev/null; then
    echo "   ❌ Docker not found. Install it first:"
    echo "      curl -fsSL https://get.docker.com | sh"
    exit 1
fi
echo "   ✅ Docker: $(docker --version)"

# Python 3
if ! command -v python3 &>/dev/null; then
    echo "   ❌ Python3 not found"
    exit 1
fi
echo "   ✅ Python: $(python3 --version 2>&1)"

# ── 2. SearXNG (через Docker Compose) ──
echo ""
echo "📋 Step 2/5: SearXNG search engine..."
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "searxng"; then
    echo "   ✅ SearXNG already running"
else
    echo "   🔄 Starting SearXNG via Docker Compose..."
    cd "$REPO_DIR"
    # Create searxng-data dir if missing
    mkdir -p searxng-data
    docker compose up -d
    # Wait for SearXNG to be ready
    echo "   ⏳ Waiting for SearXNG..."
    for i in $(seq 1 15); do
        if curl -sf http://localhost:8081/ >/dev/null 2>&1; then
            echo "   ✅ SearXNG is running on http://localhost:8081"
            break
        fi
        sleep 2
    done
    if ! curl -sf http://localhost:8081/ >/dev/null 2>&1; then
        echo "   ⚠️ SearXNG may still be starting. Check with: docker compose logs"
    fi
    cd "$REPO_DIR"
fi

# ── 3. Python virtualenv ──
echo ""
echo "📋 Step 3/5: Python virtualenv..."
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
    echo "   ✅ venv already exists"
else
    echo "   🔄 Creating venv..."
    python3 -m venv "$VENV_DIR"
    echo "   ✅ venv created"
fi

# ── 4. Python dependencies ──
echo ""
echo "📋 Step 4/5: Python packages..."
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
echo "   ✅ All packages installed"

# ── 5. Neural model ──
echo ""
echo "📋 Step 5/5: Neural reranking model..."
MODEL_CHECK="$MODEL_DIR/models--sentence-transformers--all-MiniLM-L6-v2/snapshots"
if [ -d "$MODEL_CHECK" ] && [ "$(ls -A "$MODEL_CHECK" 2>/dev/null)" ]; then
    echo "   ✅ Model already cached ($(du -sh "$MODEL_DIR" | cut -f1))"
else
    echo "   🔄 Downloading model (all-MiniLM-L6-v2, ~90MB)..."
    bash "$REPO_DIR/scripts/download_model.sh"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Deployment complete!                                 ║"
echo "║                                                          ║"
echo "║  Run the server:                                          ║"
echo "║    $VENV_DIR/bin/python server.py                        ║"
echo "║                                                          ║"
echo "║  For Claude Code, add to ~/.claude.json:                  ║"
echo '║    { "mcpServers": { "poisk-mcp": { "type": "stdio",     ║'
echo '║        "command": "'"$VENV_DIR/bin/python"'",            ║'
echo '║        "args": ["'"$REPO_DIR/server.py"'"]               ║'
echo "║      }}}                                                  ║"
echo "║                                                          ║"
echo "║  Or with global config:                                   ║"
echo "║    claude mcp add poisk-mcp --stdio                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
