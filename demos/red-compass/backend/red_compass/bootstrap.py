"""Initialize policy vectors and optional demo requests inside the API image."""

import argparse
from .retrieval import ingest
from .seed import seed_requests

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--policies-only", action="store_true")
    args = parser.parse_args()
    print(f"Indexed {ingest()} policy sections", flush=True)
    if not args.policies_only:
        print(
            f"Created {seed_requests()} initial requests; existing seeds preserved.",
            flush=True,
        )
