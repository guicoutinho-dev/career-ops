import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.explore_jobspy import run_explore_jobspy

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    try:
        raw = json.load(sys.stdin)
        filters = raw if isinstance(raw, dict) else {}
        result = run_explore_jobspy(filters, config_path=PROJECT_ROOT / "config" / "sources.yaml")
    except Exception as exc:
        logging.exception("JobSpy Explore scan failed")
        result = {
            "offers": [],
            "sources": {
                "jobspy_indeed": {"status": "failed", "matches": 0, "error": str(exc)},
                "jobspy_linkedin": {"status": "failed", "matches": 0, "error": str(exc)},
                "seek_native": {
                    "status": "blocked",
                    "matches": 0,
                    "error": "SEEK API access requires approval; no unofficial endpoint was called.",
                },
            },
            "truncated": False,
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
