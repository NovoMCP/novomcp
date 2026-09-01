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

# Copy application code — src-layout package + the root dev shim.
# The engine lives under src/novomcp/; main_https.py is a shim that puts src on
# the path, so `python main_https.py` boots the package the same as a checkout.
COPY main_https.py ./
COPY src ./src/
# Optional runtime assets (a curated openapi.json and a ui-apps directory) are
# NOT shipped in OSS and are not copied here: the engine generates the OpenAPI
# spec from its live route table (main_https.py serve_curated_openapi fallback)
# and skips UI resources when ui-apps is absent (router.py guards on exists()).
# A self-hoster who adds either to orchestrator/ can COPY it in their own build.

# Runtime environment
ENV PORT=8018
ENV PYTHONUNBUFFERED=1
ENV AWS_REGION=us-east-1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8018/health || exit 1

EXPOSE 8018

CMD ["python", "main_https.py"]
