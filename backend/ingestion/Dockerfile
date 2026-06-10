# Pinned by digest for reproducible builds. Refresh with:
#   docker manifest inspect python:3.11-slim   (use the OCI index digest)
FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install from the fully-pinned, hashed lockfile (regenerate with
# `uv pip compile requirements.txt --generate-hashes -o requirements.lock`).
# --require-hashes makes pip reject any package whose hash is not listed.
COPY ingestion/requirements.lock .
RUN python -m pip install --prefer-binary --require-hashes -r requirements.lock

COPY shared ./shared
COPY ingestion/. .

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

CMD ["python", "main.py"]
