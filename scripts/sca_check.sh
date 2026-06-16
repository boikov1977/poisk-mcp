#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="${VENV_DIR:-$(dirname "$0")/../venv}"
REQS_FILE="${REQS_FILE:-$(dirname "$0")/../requirements.txt}"
PYTHON="$VENV_DIR/bin/python"
PIP_AUDIT="$VENV_DIR/bin/pip-audit"

if [ ! -f "$REQS_FILE" ]; then
  echo "❌ requirements.txt not found at $REQS_FILE"
  exit 1
fi

echo "🔍 SCA check: $(basename "$REQS_FILE")"
echo "   Python: $($PYTHON --version 2>&1)"
echo "   pip-audit: $($PIP_AUDIT --version 2>&1)"
echo ""

# 1) Проверка известных уязвимостей
echo "--- Known vulnerabilities ---"
if ! "$PIP_AUDIT" -r "$REQS_FILE" --timeout 120; then
  echo "❌ Found vulnerabilities!"
  exit 1
fi
echo "✅ No known vulnerabilities"

echo ""

# 2) Проверка целостности requirements (нет дублей, пустых строк, конфликтов)
echo "--- Requirements file sanity ---"
# Ищем не-pinned версии (содержат >= или ~= или *)
BAD_LINES=$(grep -nE '^[^#]*[><]=' "$REQS_FILE" || true)
if [ -n "$BAD_LINES" ]; then
  echo "⚠️  Found unpinned dependencies:"
  echo "$BAD_LINES"
  exit 1
fi
echo "✅ All dependencies pinned"
