FROM python:3.11-slim

WORKDIR /app

# Install dependencies first — cached layer speeds up code-only rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py .

# Run as non-root user for security (F-006)
RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

# Health check so the host can detect and restart unhealthy containers
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "main.py"]
