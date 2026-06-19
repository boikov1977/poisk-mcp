#!/usr/bin/env bash
# ── Download FlashRank model ──
# FlashRank автоматически скачивает ONNX-модель при первом импорте.
# Этот скрипт — только для prefetch-кэша (опционально, можно не запускать).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
MODEL_NAME="ms-marco-MultiBERT-L-12"

echo "📦 Prefetching FlashRank model: $MODEL_NAME"
echo "   ~98MB, будет загружена в HF-кэш"

# Создаём временное venv для скачивания (если основного нет)
TMP_VENV=$(mktemp -d)
python3 -m venv "$TMP_VENV"
"$TMP_VENV/bin/pip" install -q flashrank 2>/dev/null

# Prefetch: простой импорт модели
"$TMP_VENV/bin/python" -c "
import os
os.environ['HF_HOME'] = '$REPO_DIR/models'
os.environ['HUGGINGFACE_HUB_CACHE'] = '$REPO_DIR/models/hub'
from flashrank import Ranker
model = Ranker(model_name='$MODEL_NAME')
print(f'✅ Model loaded: {model}')
" 2>&1

rm -rf "$TMP_VENV"
echo "✅ FlashRank model cached at $REPO_DIR/models"
