"""Verify or explicitly refresh the six architecture semantic-oracle fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.behavior_baseline_support import assert_semantic_subset
from tests.architecture_semantic_oracle import PROFILE_IDS, semantic_oracle


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "architecture_semantic_oracles"


def _path(profile_id: str) -> Path:
    return FIXTURE_ROOT / f"profile-{profile_id}.json"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "explicitly rewrite the reviewed fixtures; the default is a "
            "strictly read-only verification"
        ),
    )
    args = parser.parse_args()

    for profile_id in PROFILE_IDS:
        actual = semantic_oracle(profile_id)
        path = _path(profile_id)
        if args.update:
            _write_json(path, actual)
            print(f"WROTE {profile_id} {path}")
            continue
        expected = json.loads(path.read_text(encoding="utf-8"))
        assert_semantic_subset(actual, expected)
        assert_semantic_subset(expected, actual)
        print(f"PASS {profile_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
