FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# BUILD_LOG Journey 8 — every top-level module, not just main.py: main.py now
# imports formatting.py, and a COPY that names only main.py builds fine and then
# crashes on import (ModuleNotFoundError) at container start — the exact failure
# pingdin_platform's compute_service/Dockerfile hit in its Journey 302.
# tests/test_config_consistency.py fails the build if main.py imports a local
# module this COPY would not include.
COPY *.py ./

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
