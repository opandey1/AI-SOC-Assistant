# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached across source changes.
COPY requirements.txt .
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY src ./src
COPY data/README.md ./data/README.md

# The NSL-KDD text files are large and gitignored, so they are NOT baked into
# the image. Mount them at runtime, e.g.:
#   docker run --rm -v "$(pwd)/data:/app/data" ai-soc-assistant
#
# The default command uses the offline template path, so the container produces
# a ticket with only the dataset mounted (no LLM required). Override it to use a
# provider, e.g.:
#   docker run --rm -v "$(pwd)/data:/app/data" ai-soc-assistant \
#       python src/pipeline.py --provider ollama
CMD ["python", "src/pipeline.py", "--no-llm"]
