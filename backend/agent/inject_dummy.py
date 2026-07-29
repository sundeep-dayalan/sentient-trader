import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv, find_dotenv

# Add parent directory to path so we can import config/redis_client
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from redis_client import create_redis_client
from replay import REPLAY_FIXTURES

# Try to find a root/parent .env file first for local development.
# If not found (e.g. in production/Docker), it will safely fallback.
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=True)
else:
    load_dotenv(override=True)


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
        print(
            "Make sure your Redis credentials are set or a local Redis/Valkey server is running."
        )
        return

    print(f"\nInjecting test news into stream '{stream_key}':")
    print(f"  Ticker:   {ticker.upper()}")
    print(f"  Headline: {headline}")
    if summary:
        print(f"  Summary:  {summary}")

    entry_id = redis.xadd(stream_key, message, id="*")
    print(f"\n[Success] Injected! Entry ID in Redis: {entry_id}")
    print("If your agent is running, it should process this message immediately!")


def inject_replay_fixtures() -> None:
    """Seed the three replay fixtures for a no-key local demo.

    Publication time is stamped now, not baked into the fixture, so the agent's
    freshness gate accepts the seed whenever it runs. Every entry carries
    is_simulated="true", so the risk gate blocks any resulting BUY or SELL from
    reaching Alpaca exactly as it does for the Signal Injector.
    """
    redis = create_redis_client()
    stream_key = os.environ.get("REDIS_STREAM_KEY", "market-news")

    print("Connecting to Redis...")
    try:
        redis.ping()
        print("Connected successfully!")
    except Exception as e:
        print(f"Error connecting to Redis: {e}")
        print(
            "Make sure your Redis credentials are set or a local Redis/Valkey server is running."
        )
        return

    published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"\nSeeding {len(REPLAY_FIXTURES)} replay fixtures into '{stream_key}':")
    for fixture in REPLAY_FIXTURES:
        entry_id = redis.xadd(
            stream_key,
            fixture.stream_fields(published_at=published_at),
            id="*",
        )
        print(f"  [{fixture.case}] {fixture.ticker}: entry {entry_id}")

    print(
        "\n[Success] Seeded. Start the agent with REPLAY_MODE=true to analyze them.\n"
        "A headline already seen inside the two-hour headline cache is skipped as "
        "a duplicate; see docs/LOCAL_DEMO.md."
    )


if __name__ == "__main__":
    if "--replay" in sys.argv[1:]:
        print("Sentient Trader Replay Fixture Seeder")
        print("=======================================")
        inject_replay_fixtures()
    else:
        print("Sentient Trader — Local Signal Injector")
        print("=======================================")
        ticker = input("Enter Stock Ticker (default: AAPL): ").strip() or "AAPL"
        headline = (
            input(
                "Enter News Headline (default: Apple launches revolutionary new AI chip with 10x performance): "
            ).strip()
            or "Apple launches revolutionary new AI chip with 10x performance"
        )
        summary = input("Enter Summary (optional, press Enter to skip): ").strip()

        inject_message(ticker, headline, summary if summary else None)
