# RAG service image. Owner: person 2.
# Build context is the REPO ROOT, because contracts/ must be installed too.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY contracts/ /app/contracts/
COPY ragsvc/ /app/ragsvc/

RUN pip install --no-cache-dir -e /app/contracts -e /app/ragsvc

WORKDIR /app/ragsvc
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
  CMD curl -fsS http://localhost:8001/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
