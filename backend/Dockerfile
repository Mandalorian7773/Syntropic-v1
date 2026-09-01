# Backend image. Owner: person 3.
# Build context is the REPO ROOT, because contracts/ must be installed too.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY contracts/ /app/contracts/
COPY backend/ /app/backend/

# --no-cache-dir keeps the image small; on the demo host add
# --no-index --find-links=/wheels to make a network fetch impossible.
RUN pip install --no-cache-dir -e /app/contracts -e /app/backend

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
  CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
