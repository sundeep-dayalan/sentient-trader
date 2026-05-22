"""
Health Check Script for Sentient Trader Ingestion Service
Verifies that the ingestion service is actively polling and updating its heartbeat in Redis.
Used by Docker healthchecks.
"""

import os
import sys
import time

try:
    import redis
except ImportError:
    print("Redis library not found, marking unhealthy.", file=sys.stderr)
    sys.exit(1)

def main():
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", 6379))
    db = int(os.environ.get("REDIS_DB", 0))
    password = os.environ.get("REDIS_PASSWORD", "") or None

    try:
        r = redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
        heartbeat = r.get("ingestion:heartbeat")
        
        if not heartbeat:
            print("No heartbeat found in Redis for ingestion service.", file=sys.stderr)
            sys.exit(1)
            
        last_updated = int(heartbeat)
        now = int(time.time())
        diff = now - last_updated
        
        # We allow up to 120 seconds of drift/inactivity
        if diff > 120:
            print(f"Ingestion heartbeat is stale by {diff} seconds.", file=sys.stderr)
            sys.exit(1)
            
        print("Ingestion service is healthy.")
        sys.exit(0)
    except Exception as e:
        print(f"Healthcheck failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
