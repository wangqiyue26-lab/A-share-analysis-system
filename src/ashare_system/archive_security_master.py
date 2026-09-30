from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .data.security_master import (
    REQUIRED_EXCHANGES,
    SecurityMasterSnapshotStore,
    validate_security_master,
)


@dataclass(frozen=True)
class SecurityMasterArchiveResult:
    snapshot_file: str
    manifest_file: str
    observed_at: str
    row_count: int
    exchanges: tuple[str, ...]
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot_file": self.snapshot_file,
            "manifest_file": self.manifest_file,
            "observed_at": self.observed_at,
            "row_count": self.row_count,
            "exchanges": list(self.exchanges),
            "sha256": self.sha256,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_latest_security_master(
    *,
    cache_root: str | Path = "data/cache",
    output_dir: str | Path = "reports/security_master_archive",
) -> SecurityMasterArchiveResult:
    """Copy the latest complete observable security-master snapshot into an artifact directory.

    The source remains the append-only local/cache snapshot store. This function creates a
    portable, checksum-addressed workflow artifact copy so historical universe observations
    can survive independently of GitHub Actions cache eviction.
    """
    store = SecurityMasterSnapshotStore(cache_root)
    source_paths = store._snapshot_paths()
    if not source_paths:
        raise FileNotFoundError(f"No security-master snapshots under {Path(cache_root) / 'security_master'}")

    source = source_paths[-1]
    master = validate_security_master(pd.read_parquet(source))
    exchanges = tuple(sorted(master["exchange"].astype(str).unique()))
    if set(exchanges) != REQUIRED_EXCHANGES:
        missing = sorted(REQUIRED_EXCHANGES - set(exchanges))
        raise ValueError(f"Refusing to archive incomplete security master; missing={missing}")

    observed_at = pd.Timestamp(master["observed_at"].max())
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")
    stamp = observed_at.strftime("%Y%m%dT%H%M%SZ")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_file = destination / f"security_master_{stamp}.parquet"
    shutil.copy2(source, snapshot_file)
    checksum = _sha256(snapshot_file)

    manifest_file = destination / "manifest.json"
    manifest = {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "row_count": len(master),
        "exchanges": list(exchanges),
        "source_cache_file": source.name,
        "snapshot_file": snapshot_file.name,
        "sha256": checksum,
        "strict_point_in_time_eligible": True,
        "note": (
            "This artifact records a universe snapshot actually observed at the stated timestamp; "
            "it must not be substituted for dates earlier than observed_at."
        ),
    }
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return SecurityMasterArchiveResult(
        snapshot_file=str(snapshot_file),
        manifest_file=str(manifest_file),
        observed_at=observed_at.isoformat(),
        row_count=len(master),
        exchanges=exchanges,
        sha256=checksum,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.archive_security_master")
    parser.add_argument("--cache-root", default="data/cache")
    parser.add_argument("--output-dir", default="reports/security_master_archive")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = archive_latest_security_master(cache_root=args.cache_root, output_dir=args.output_dir)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
