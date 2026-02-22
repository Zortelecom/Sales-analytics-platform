#!/usr/bin/env python3
"""CLI for serving layer operations"""
import argparse
import sys
from .sync import ServingLayerSync
from .config import ServingConfig


def main():
    parser = argparse.ArgumentParser(
        description="SQLMesh-aligned DuckLake Serving Sync"
    )

    parser.add_argument(
        "--env",
        default="dev",
        help="SQLMesh environment (dev, prod, staging, ...)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true")

    args = parser.parse_args()

    config = ServingConfig(environment=args.env)
    config.normalize()

    sync = ServingLayerSync(config)

    if args.validate:
        sys.exit(0 if sync.validate_serving_db() else 1)

    sync.sync(dry_run=args.dry_run)



if __name__ == "__main__":
    main()
