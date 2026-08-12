# PrivaParse gateway.
#
# Two targets. `slim` is the software; it downloads the detection model on
# first use. `full` bakes the weights in and goes offline, which is what you
# want on a machine that should never reach the Hugging Face Hub -- and what
# you want anyway if the container is ever restarted, since the alternative is
# re-downloading 1.2 GB.
#
#   docker build --target slim -t privaparse:slim .
#   docker build --target full -t privaparse:full .
#
# Run it. The gateway binds loopback and refuses anything else, because the
# vault beside it holds plaintext values and has no per-user access control:
#
#   docker run --rm --network host -v privaparse-vault:/data privaparse:full
#
# `--network host` is what makes a loopback bind inside the container reachable
# from the host, and it is deliberately the only documented way in. Publishing
# a port instead would mean binding 0.0.0.0 inside the container, and this
# image has no way to ask for that.

FROM python:3.12-slim AS base

# curl is for a container healthcheck, nothing at runtime needs it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/privaparse

# The package metadata alone first, so a change to the source does not
# reinstall torch.
COPY pyproject.toml README.md LICENSE ./
COPY privaparse/__init__.py privaparse/__init__.py
RUN pip install --no-cache-dir ".[gateway,model]"

COPY privaparse ./privaparse
COPY alembic.ini ./
RUN pip install --no-cache-dir --no-deps -e .

# The vault lives on a volume. It is the most sensitive file the tool
# produces, and it must outlive the container rather than being rebuilt --
# a lost vault is every past answer left unrestorable.
RUN mkdir -p /data && useradd --system --uid 10001 privaparse \
 && chown -R privaparse:privaparse /data /opt/privaparse
ENV PRIVAPARSE_DB_PATH=/data/privaparse.db \
    PRIVAPARSE_MODEL_DIR=/opt/privaparse/models
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=120s \
    CMD curl -fsS http://127.0.0.1:8787/healthz || exit 1

USER privaparse
CMD ["privaparse", "serve", "--host", "127.0.0.1", "--port", "8787"]


# --- slim: no weights ------------------------------------------------------
#
# Smaller image, first request pays for a 1.2 GB download. Mount a volume at
# /opt/privaparse/models if you want that download to survive a restart.

FROM base AS slim


# --- full: weights baked in, Hub never contacted ---------------------------

FROM base AS full

USER root
RUN mkdir -p /opt/privaparse/models && chown privaparse:privaparse /opt/privaparse/models
USER privaparse

# Force the lazy detector to load once, which is what pulls the weights into
# PRIVAPARSE_MODEL_DIR. The throwaway vault is deleted again: an image must
# not ship with somebody's database in it.
RUN PRIVAPARSE_DB_PATH=/tmp/build.db python -c \
      "from privaparse.engine import PrivaParseEngine; \
       PrivaParseEngine(configure_logs=False).detector" \
 && rm -f /tmp/build.db

# Nothing reaches the Hub from here. Without this every start revalidates the
# cached model over the network, which is a strange thing for a tool whose
# whole promise is that the document never leaves the machine.
ENV PRIVAPARSE_OFFLINE=1
