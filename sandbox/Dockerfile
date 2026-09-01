# Sandbox for execute_python. Owner: person 3.
#
# Deliberately minimal. Every package here is one the agent can import; every
# package NOT here is one it cannot. Adding to this list is a decision, not a
# convenience -- the sandbox is the security boundary.
#
# Build once at setup time, offline forever after:
#   docker build -t sih26117-sandbox ./sandbox
FROM python:3.11-slim

RUN pip install --no-cache-dir \
      numpy \
      pandas \
      matplotlib \
      openpyxl \
 && rm -rf /root/.cache

# matplotlib without a display: pick the non-interactive backend up front.
ENV MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 sandbox
USER sandbox
WORKDIR /workspace

# Run with: --network none --read-only --tmpfs /tmp -v $WORKSPACE_DIR:/workspace
CMD ["python3"]
