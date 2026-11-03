from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data.security_master import SecurityMasterSnapshotStore, validate_security_master

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MASTER_STATE_COLUMNS = (
    "symbol",
    "name",
    "exchange",
    "board",
    "list_date",
    "industry",
    "is_st",
    "is_listed",
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8")


def _normalize_master_state(frame: pd.DataFrame) -> pd.DataFrame:
    master = validate_security_master(frame)
    state = master.loc[:, MASTER_STATE_COLUMNS].copy()
    state["symbol"] = state["symbol"].astype(str).str.zfill(6)
    state["list_date"] = pd.to_datetime(state["list_date"], errors="coerce")
    state["industry"] = state["industry"].fillna("").astype(str)
    return state.sort_values("symbol").reset_index(drop=True)


def _to_bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    invalid = ~normalized.isin({"true", "false", "1", "0"})
    if invalid.any():
        values = sorted(normalized[invalid].unique())
        raise ValueError(f"Unsupported boolean values: {values[:10]}")
    return normalized.isin({"true", "1"})


def _normalize_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "name",
        "exchange",
        "board",
        "list_date",
        "is_st",
        "is_listed",
        "amount",
        "screen_source",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Candidate evidence missing columns: {missing}")

    result = frame.copy()
    result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce")
    result["is_st"] = _to_bool_series(result["is_st"])
    result["is_listed"] = _to_bool_series(result["is_listed"])
    result["amount"] = pd.to_numeric(result["amount"], errors="raise")
    if result["symbol"].duplicated().any():
        raise ValueError("Candidate evidence contains duplicate symbols")
    return result.sort_values(["amount", "symbol"], ascending=[False, True]).reset_index(drop=True)


@dataclass(frozen=True)
class PITEvidenceStageResult:
    evidence_date: str
    security_master_observed_at: str
    master_state_sha256: str
    candidate_sha256: str
    candidate_count: int
    screen_source: str
    degraded_mode: bool
    staging_dir: str

    def as_dict(self) -> dict[str, object]:
        return {
            "evidence_date": self.evidence_date,
            "security_master_observed_at": self.security_master_observed_at,
            "master_state_sha256": self.master_state_sha256,
            "candidate_sha256": self.candidate_sha256,
            "candidate_count": self.candidate_count,
            "screen_source": self.screen_source,
            "degraded_mode": self.degraded_mode,
            "staging_dir": self.staging_dir,
        }


def stage_pit_evidence(
    *,
    cache_root: str | Path,
    candidates_file: str | Path,
    universe_manifest_file: str | Path,
    output_dir: str | Path,
) -> PITEvidenceStageResult:
    """Create a portable point-in-time evidence bundle from the actual daily universe run."""
    manifest_path = Path(universe_manifest_file)
    universe_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_date = pd.Timestamp(universe_manifest["prepared_as_of"]).date().isoformat()

    master_store = SecurityMasterSnapshotStore(cache_root)
    master = master_store.load_latest()
    state = _normalize_master_state(master)
    candidates = _normalize_candidates(pd.read_csv(candidates_file, dtype={"symbol": str}))

    master_symbols = set(state["symbol"])
    missing_symbols = sorted(set(candidates["symbol"]) - master_symbols)
    if missing_symbols:
        raise ValueError(
            "Candidate evidence contains symbols absent from the observed security master: "
            f"{missing_symbols[:10]}"
        )

    observed_at = pd.Timestamp(master["observed_at"].max())
    if observed_at.tzinfo is None:
        observed_at = observed_at.tz_localize("UTC")
    else:
        observed_at = observed_at.tz_convert("UTC")

    manifest_observed_at = pd.Timestamp(universe_manifest["security_master_observed_at"])
    if manifest_observed_at.tzinfo is None:
        manifest_observed_at = manifest_observed_at.tz_localize("UTC")
    else:
        manifest_observed_at = manifest_observed_at.tz_convert("UTC")
    if observed_at != manifest_observed_at:
        raise RuntimeError(
            "Latest cached security master does not match the exact master used by "
            f"the universe run: cache={observed_at.isoformat()} "
            f"manifest={manifest_observed_at.isoformat()}"
        )

    master_check = state.loc[
        :,
        ["symbol", "exchange", "board", "list_date", "is_st", "is_listed"],
    ].copy()
    candidate_check = candidates.loc[
        :,
        ["symbol", "exchange", "board", "list_date", "is_st", "is_listed"],
    ].copy()
    joined = candidate_check.merge(
        master_check,
        on="symbol",
        how="left",
        suffixes=("_candidate", "_master"),
        validate="one_to_one",
    )
    for column in ("exchange", "board", "is_st", "is_listed"):
        left = joined[f"{column}_candidate"].astype(str)
        right = joined[f"{column}_master"].astype(str)
        if not left.equals(right):
            raise RuntimeError(
                f"Candidate metadata disagrees with observed security master for {column}"
            )
    left_dates = pd.to_datetime(joined["list_date_candidate"], errors="coerce")
    right_dates = pd.to_datetime(joined["list_date_master"], errors="coerce")
    if not left_dates.equals(right_dates):
        raise RuntimeError("Candidate list_date disagrees with observed security master")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    master_path = output / "master_state.csv"
    candidate_path = output / "candidates.csv"
    state_bytes = _canonical_csv_bytes(state)
    candidate_bytes = _canonical_csv_bytes(candidates)
    master_path.write_bytes(state_bytes)
    candidate_path.write_bytes(candidate_bytes)

    screen_sources = sorted(set(candidates["screen_source"].astype(str)))
    if len(screen_sources) != 1:
        raise ValueError(f"Expected one candidate screen source, got {screen_sources}")
    screen_source = screen_sources[0]

    payload = {
        "schema_version": 1,
        "evidence_date": evidence_date,
        "security_master_observed_at": observed_at.isoformat(),
        "master_state_sha256": _sha256_bytes(state_bytes),
        "candidate_sha256": _sha256_bytes(candidate_bytes),
        "candidate_count": len(candidates),
        "screen_source": screen_source,
        "degraded_mode": bool(universe_manifest.get("degraded_mode", False)),
        "spot_provider_source": universe_manifest.get("spot_provider_source"),
        "spot_coverage": universe_manifest.get("spot_coverage"),
        "listing_age_verified": bool(universe_manifest.get("listing_age_verified", False)),
        "security_master_source": universe_manifest.get("security_master_source"),
        "universe_manifest_sha256": _sha256_file(manifest_path),
        "strict_point_in_time_evidence": True,
        "note": (
            "This bundle records the candidate set and security-master state actually observed "
            "by the daily production universe run. It must not be used for dates before "
            "evidence_date."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return PITEvidenceStageResult(
        evidence_date=evidence_date,
        security_master_observed_at=observed_at.isoformat(),
        master_state_sha256=payload["master_state_sha256"],
        candidate_sha256=payload["candidate_sha256"],
        candidate_count=len(candidates),
        screen_source=screen_source,
        degraded_mode=payload["degraded_mode"],
        staging_dir=str(output),
    )


@dataclass(frozen=True)
class PITEvidencePersistResult:
    status: str
    evidence_date: str
    day_manifest: str | None
    candidate_file: str | None
    master_state_file: str | None
    master_state_created: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "evidence_date": self.evidence_date,
            "day_manifest": self.day_manifest,
            "candidate_file": self.candidate_file,
            "master_state_file": self.master_state_file,
            "master_state_created": self.master_state_created,
        }


def persist_staged_evidence(
    *,
    staging_dir: str | Path,
    ledger_root: str | Path,
    now: pd.Timestamp | None = None,
    market_close_guard: bool = True,
) -> PITEvidencePersistResult:
    """Persist staged evidence into an immutable, Git-friendly ledger.

    Candidate evidence is saved every eligible trading day. The full security-master
    state is content-addressed and deduplicated: a new 5k+ row file is only added when
    membership/status metadata actually changes.
    """
    staging = Path(staging_dir)
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    evidence_date = pd.Timestamp(manifest["evidence_date"]).date().isoformat()

    current = now if now is not None else pd.Timestamp.now(tz=CHINA_TZ)
    if current.tzinfo is None:
        current = current.tz_localize(CHINA_TZ)
    else:
        current = current.tz_convert(CHINA_TZ)

    if market_close_guard:
        same_day = current.date().isoformat() == evidence_date
        after_close = current.time() >= time(15, 10)
        if not same_day or not after_close:
            return PITEvidencePersistResult(
                status="skipped_market_clock",
                evidence_date=evidence_date,
                day_manifest=None,
                candidate_file=None,
                master_state_file=None,
                master_state_created=False,
            )

    master_source = staging / "master_state.csv"
    candidate_source = staging / "candidates.csv"
    if _sha256_file(master_source) != manifest["master_state_sha256"]:
        raise RuntimeError("Staged security-master state checksum mismatch")
    if _sha256_file(candidate_source) != manifest["candidate_sha256"]:
        raise RuntimeError("Staged candidate checksum mismatch")

    ledger = Path(ledger_root)
    state_dir = ledger / "security_master" / "states"
    day_dir = ledger / "days"
    candidate_dir = ledger / "candidates"
    state_dir.mkdir(parents=True, exist_ok=True)
    day_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)

    state_name = f"{manifest['master_state_sha256'][:20]}.csv"
    state_path = state_dir / state_name
    state_created = False
    if state_path.exists():
        if _sha256_file(state_path) != manifest["master_state_sha256"]:
            raise RuntimeError(f"Content-addressed state collision at {state_path}")
    else:
        shutil.copy2(master_source, state_path)
        state_created = True

    candidate_path = candidate_dir / f"{evidence_date}.csv"
    if candidate_path.exists():
        existing_hash = _sha256_file(candidate_path)
        if existing_hash != manifest["candidate_sha256"]:
            raise RuntimeError(
                f"Refusing to rewrite immutable PIT candidate evidence for {evidence_date}"
            )
    else:
        shutil.copy2(candidate_source, candidate_path)

    day_path = day_dir / f"{evidence_date}.json"
    day_payload = {
        **manifest,
        "master_state_file": state_path.relative_to(ledger).as_posix(),
        "candidate_file": candidate_path.relative_to(ledger).as_posix(),
    }
    serialized = json.dumps(day_payload, ensure_ascii=False, indent=2) + "\n"
    if day_path.exists():
        existing = json.loads(day_path.read_text(encoding="utf-8"))
        if existing != day_payload:
            raise RuntimeError(
                f"Refusing to rewrite immutable PIT day manifest for {evidence_date}"
            )
        status = "idempotent"
    else:
        day_path.write_text(serialized, encoding="utf-8")
        status = "persisted"

    return PITEvidencePersistResult(
        status=status,
        evidence_date=evidence_date,
        day_manifest=str(day_path),
        candidate_file=str(candidate_path),
        master_state_file=str(state_path),
        master_state_created=state_created,
    )


class PITEvidenceStore:
    """Read immutable daily candidate evidence without future substitution."""

    def __init__(self, root: str | Path = "research/pit") -> None:
        self.root = Path(root)

    def available_dates(self) -> list[pd.Timestamp]:
        day_dir = self.root / "days"
        return sorted(
            pd.Timestamp(path.stem).normalize()
            for path in day_dir.glob("*.json")
            if path.stem[:4].isdigit()
        )

    def _day_manifest(self, date: str | pd.Timestamp) -> dict[str, object]:
        normalized = pd.Timestamp(date).normalize()
        path = self.root / "days" / f"{normalized.date().isoformat()}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"No exact PIT evidence for {normalized.date().isoformat()} under {self.root}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def load_candidates(self, date: str | pd.Timestamp) -> pd.DataFrame:
        manifest = self._day_manifest(date)
        path = self.root / str(manifest["candidate_file"])
        if _sha256_file(path) != manifest["candidate_sha256"]:
            raise RuntimeError(f"PIT candidate checksum mismatch: {path}")
        return _normalize_candidates(pd.read_csv(path, dtype={"symbol": str}))

    def load_master_for_date(self, date: str | pd.Timestamp) -> pd.DataFrame:
        manifest = self._day_manifest(date)
        path = self.root / str(manifest["master_state_file"])
        if _sha256_file(path) != manifest["master_state_sha256"]:
            raise RuntimeError(f"PIT security-master checksum mismatch: {path}")
        frame = pd.read_csv(path, dtype={"symbol": str})
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
        frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
        frame["is_st"] = _to_bool_series(frame["is_st"])
        frame["is_listed"] = _to_bool_series(frame["is_listed"])
        return frame.loc[:, MASTER_STATE_COLUMNS].sort_values("symbol").reset_index(drop=True)

    def load_master_as_of(self, as_of: str | pd.Timestamp) -> pd.DataFrame:
        """Use only a ledger observation dated at or before the requested cutoff."""
        cutoff = pd.Timestamp(as_of).normalize()
        eligible = [date for date in self.available_dates() if date <= cutoff]
        if not eligible:
            raise FileNotFoundError(
                "No PIT evidence exists at or before "
                f"{cutoff.date().isoformat()}; future/current state substitution is forbidden"
            )
        return self.load_master_for_date(eligible[-1])

    def day_manifest(self, date: str | pd.Timestamp) -> dict[str, object]:
        return self._day_manifest(date)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ashare_system.pit_evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    stage = sub.add_parser("stage")
    stage.add_argument("--cache-root", default="data/cache")
    stage.add_argument("--candidates-file", required=True)
    stage.add_argument("--universe-manifest", required=True)
    stage.add_argument("--output-dir", default="reports/pit-evidence")

    persist = sub.add_parser("persist")
    persist.add_argument("--staging-dir", default="reports/pit-evidence")
    persist.add_argument("--ledger-root", default="research/pit")
    persist.add_argument("--no-market-close-guard", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "stage":
        result = stage_pit_evidence(
            cache_root=args.cache_root,
            candidates_file=args.candidates_file,
            universe_manifest_file=args.universe_manifest,
            output_dir=args.output_dir,
        )
    else:
        result = persist_staged_evidence(
            staging_dir=args.staging_dir,
            ledger_root=args.ledger_root,
            market_close_guard=not args.no_market_close_guard,
        )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
