#!/usr/bin/env bash
# ── Download sentence-transformers model ──
# This script downloads the neural reranking model
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
MODEL_DIR="$REPO_DIR/models"
MODEL_NAME="sentence-transformers/all-MiniLM-L6-v2"

echo "📦 Downloading model: $MODEL_NAME"
echo "   Target: $MODEL_DIR"

# Create a temporary venv just to download the model
TMP_VENV=$(mktemp -d)
python3 -m venv "$TMP_VENV"
"$TMP_VENV/bin/pip" install -q sentence-transformers==5.5.0 2>/dev/null

# Download model explicitly
"$TMP_VENV/bin/python" -c "
import os
os.environ['TRANSFORMERS_CACHE'] = '$MODEL_DIR'
os.environ['HUGGINGFACE_HUB_CACHE'] = '$MODEL_DIR/hub'
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('$MODEL_NAME', cache_folder='$MODEL_DIR')
print(f'✅ Model downloaded: {model}')
" 2>&1

rm -rf "$TMP_VENV"
echo "✅ Model ready at $MODEL_DIR"
