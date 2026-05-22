import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

# Add parent directory to path so we can import config/redis_client
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from redis_client import create_redis_client

load_dotenv()

def inject_message(ticker: str, headline: str, summary: str = None) -> None:
    redis = create_redis_client()
    stream_key = os.environ.get("REDIS_STREAM_KEY", "market-news")
    
    message = {
        "ticker": ticker.upper(),
        "headline": headline,
        "source": "manual_simulation",
        "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "is_simulated": "true",
    }
    if summary:
        message["summary"] = summary

    print("Connecting to Redis...")
    try:
        # Check connection
        redis.ping()
        print("Connected successfully!")
    except Exception as e:
        print(f"Error connecting to Redis: {e}")
        print("Make sure your Redis credentials are set or a local Redis/Valkey server is running.")
        return

    print(f"\nInjecting test news into stream '{stream_key}':")
    print(f"  Ticker:   {ticker.upper()}")
    print(f"  Headline: {headline}")
    if summary:
        print(f"  Summary:  {summary}")
        
    entry_id = redis.xadd(stream_key, message, id="*")
    print(f"\n[Success] Injected! Entry ID in Redis: {entry_id}")
    print("If your agent is running, it should process this message immediately!")

if __name__ == "__main__":
    print("Sentient Trader — Local Signal Injector")
    print("=======================================")
    ticker = input("Enter Stock Ticker (default: AAPL): ").strip() or "AAPL"
    headline = input("Enter News Headline (default: Apple launches revolutionary new AI chip with 10x performance): ").strip() or "Apple launches revolutionary new AI chip with 10x performance"
    summary = input("Enter Summary (optional, press Enter to skip): ").strip()
    
    inject_message(ticker, headline, summary if summary else None)
