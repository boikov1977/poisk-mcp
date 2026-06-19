VENV      := venv
PYTHON    := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
REQS      := requirements.txt
SCRIPTS   := scripts

.PHONY: dev venv check sca test clean

dev: venv
	@echo "✅ Development environment ready"

venv: $(VENV)/bin/python
	@echo "✅ Virtualenv ready"

$(VENV)/bin/python:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install -r $(REQS)

# CPU-only PyTorch (без CUDA — в 10 раз быстрее установка)
install-cpu: venv
	$(PIP) install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
	$(PIP) install -r $(REQS)

check: sca

sca:
	@echo "🔍 Software Composition Analysis"
	VENV_DIR=$(VENV) bash $(SCRIPTS)/sca_check.sh

test:
	@echo "🧪 Running tests..."
	$(PYTHON) -m pytest tests -v --tb=short || true

clean:
	rm -rf $(VENV)
	rm -rf __pycache__ .pytest_cache .ruff_cache
	find . -name '*.pyc' -delete
