# NovoMCP engine — container image.
#
# Build context is the engine source directory (containing this
# Dockerfile). All COPY paths are relative to that context.
FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements + install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code — top-level Python + package dirs
COPY *.py ./
COPY faves_reference_lists.json ./
COPY openapi.json ./
COPY core ./core/
COPY ai ./ai/
COPY routers ./routers/
COPY models ./models/
COPY monitoring ./monitoring/
COPY utils ./utils/
COPY mcp ./mcp/
COPY ui-apps ./ui-apps/
COPY data ./data/

# Runtime environment
ENV PORT=8018
ENV PYTHONUNBUFFERED=1
ENV AWS_REGION=us-east-1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8018/health || exit 1

EXPOSE 8018

CMD ["python", "main_https.py"]
