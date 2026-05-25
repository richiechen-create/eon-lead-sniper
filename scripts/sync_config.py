"""YAML → DB sync.

Usage:  python -m scripts.sync_config [--config-dir config]
"""
import argparse
import json
from pathlib import Path

from app.db import session_scope
from app.sync.yaml_config import sync_all


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default="config", help="Directory containing YAML files")
    args = parser.parse_args()

    config_dir = Path(args.config_dir).resolve()
    with session_scope() as session:
        report = sync_all(session, config_dir)
    print(json.dumps(report.as_dict(), indent=2))


if __name__ == "__main__":
    main()
