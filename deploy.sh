#!/usr/bin/env bash
set -euo pipefail

# ═════════════════════════════════════════════════════════════════
#  MCP SearchTool v3.5 — Deploy Script
#  One-command setup: SearXNG + Python venv + зависимости
#  (PyTorch не нужен — FlashRank использует ONNX Runtime)
# ═════════════════════════════════════════════════════════════════

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/venv"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  🚀 MCP SearchTool v3.5 — Deploy                        ║"
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
    # Generate secure secret key if not already set
    if [ ! -f .env ] || ! grep -q "SEARXNG_SECRET_KEY" .env 2>/dev/null; then
        echo "SEARXNG_SECRET_KEY=$(openssl rand -hex 32)" >> .env
        echo "   🔑 Generated SEARXNG_SECRET_KEY in .env"
    fi
    docker compose --env-file .env up -d
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
echo "   ✅ All packages installed (FlashRank сам скачает модель при первом запуске)"

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
