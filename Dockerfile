FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/huggingface

WORKDIR /app

# Install the project and the backend's optional model/parser dependencies.
COPY pyproject.toml README.md ./
COPY src ./src
COPY apps ./apps
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[ai,parsers]"

# Cloud Run injects PORT. Run one process: the current indexes and job status
# are process-local and are not safe to split across multiple workers.
RUN useradd --create-home --uid 10001 assistant \
    && mkdir -p /app/data/chroma /app/data \
    && chown -R assistant:assistant /app
USER assistant

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
