# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached across source changes.
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app --home /home/app app

COPY --chown=app:app src ./src
COPY --chown=app:app streamlit_app.py ./streamlit_app.py
COPY --chown=app:app .streamlit ./.streamlit
COPY --chown=app:app data/README.md ./data/README.md
RUN mkdir -p /app/state /app/models \
    && chown -R app:app /app/state /app/models

# The NSL-KDD text files are large and gitignored, so they are NOT baked into
# the image. Mount them at runtime, e.g.:
#   docker run --rm -v "$(pwd)/data:/app/data:ro" ai-soc-assistant
#
# The image runs without root privileges. Mount the dataset read-only whenever
# possible; the pipeline does not modify the raw NSL-KDD files.
USER app

# Deterministic template mode is the safe default: no LLM and no network lookup.
CMD ["python", "-m", "src.pipeline", "--no-llm"]
