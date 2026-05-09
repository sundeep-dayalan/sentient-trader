FROM python:3.11-slim

WORKDIR /app

# Install dependencies first — cached layer speeds up code-only rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py .

CMD ["python", "main.py"]
