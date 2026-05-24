"""Docker healthcheck wrapper for the ingestion worker."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from shared.worker_health import run_worker_healthcheck


def main() -> None:
    run_worker_healthcheck("ingestion")


if __name__ == "__main__":
    main()
