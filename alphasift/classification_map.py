"""Fact-only industry, concept, and supply-chain classification mapping."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

OUTPUT_COLUMNS = [
    "code",
    "name",
    "industry",
    "concepts",
    "supply_chain",
    "industry_source",
    "concepts_source",
    "supply_chain_source",
    "updated_at",
]
_SOURCES = ("snapshot", "existing_map", "supply_chain_file", "empty")
_LABEL_SEPARATOR_RE = re.compile(r"\s*[,，、;；|]\s*")
_CODE_RE = re.compile(r"^\d{6}$")
_SUPPLY_CHAIN_COLUMNS = {"code", "industry", "concepts", "supply_chain"}


def build_classification_map(
    snapshot: pd.DataFrame,
    *,
    existing_map: pd.DataFrame | None = None,
    supply_chain_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Build a stable, source-labelled fact mapping without inference."""
    if not isinstance(snapshot, pd.DataFrame):
        raise TypeError("snapshot must be a pandas DataFrame")

    errors: list[str] = []
    snapshot_rows: dict[str, list[dict[str, object]]] = {}
    snapshot_first_rows: dict[str, dict[str, object]] = {}
    duplicate_code_count = 0
    invalid_code_count = 0

    if "code" not in snapshot.columns:
        invalid_code_count = len(snapshot)
        if invalid_code_count:
            errors.append(f"snapshot_missing_columns:code rows={invalid_code_count}")
    else:
        for row in snapshot.to_dict(orient="records"):
            code = _valid_code(row.get("code"))
            if not code:
                invalid_code_count += 1
                errors.append(f"snapshot_invalid_code:{_display(row.get('code'))}")
                continue
            snapshot_rows.setdefault(code, []).append(row)
            if code in snapshot_first_rows:
                duplicate_code_count += 1
                errors.append(f"snapshot_duplicate_code:{code}")
            else:
                snapshot_first_rows[code] = row

    existing_rows = _index_auxiliary_rows(existing_map, "existing_map", errors)
    supply_rows = _load_supply_chain_rows(supply_chain_path, errors)

    output_rows: list[dict[str, object]] = []
    source_counts = {
        field: {source: 0 for source in _SOURCES}
        for field in ("industry", "concepts", "supply_chain")
    }
    for code, snapshot_row in snapshot_first_rows.items():
        existing_row = existing_rows.get(code, {})
        supply_row = supply_rows.get(code, {})
        industry, industry_source = _choose_field(
            snapshot_row, existing_row, supply_row, "industry"
        )
        concepts, concepts_source = _choose_field(
            snapshot_row, existing_row, supply_row, "concepts", normalize_label=True
        )
        supply_chain, supply_chain_source = _choose_field(
            snapshot_row,
            existing_row,
            supply_row,
            "supply_chain",
            normalize_label=True,
        )
        output_rows.append(
            {
                "code": code,
                "name": _choose_text(snapshot_row, existing_row, "name"),
                "industry": industry,
                "concepts": concepts,
                "supply_chain": supply_chain,
                "industry_source": industry_source,
                "concepts_source": concepts_source,
                "supply_chain_source": supply_chain_source,
                "updated_at": _latest_updated_at(
                    *snapshot_rows.get(code, []),
                    *existing_row.pop("__all_rows", []),
                ),
            }
        )
        for field, source in (
            ("industry", industry_source),
            ("concepts", concepts_source),
            ("supply_chain", supply_chain_source),
        ):
            source_counts[field][source] += 1

    result = pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS)
    audit = {
        "total_snapshot_rows": int(len(snapshot)),
        "output_rows": int(len(result)),
        "unique_code_count": int(len(result)),
        "duplicate_code_count": int(duplicate_code_count),
        "invalid_code_count": int(invalid_code_count),
        "industry_coverage_count": int(result["industry"].ne("").sum()) if not result.empty else 0,
        "concepts_coverage_count": int(result["concepts"].ne("").sum()) if not result.empty else 0,
        "supply_chain_coverage_count": int(result["supply_chain"].ne("").sum()) if not result.empty else 0,
        "missing_industry_codes": _missing_codes(result, "industry"),
        "missing_concepts_codes": _missing_codes(result, "concepts"),
        "missing_supply_chain_codes": _missing_codes(result, "supply_chain"),
        "source_counts": source_counts,
        "errors": errors,
    }
    return result, audit


def _index_auxiliary_rows(
    frame: pd.DataFrame | None,
    source: str,
    errors: list[str],
) -> dict[str, dict[str, object]]:
    if frame is None:
        return {}
    if not isinstance(frame, pd.DataFrame):
        errors.append(f"{source}_invalid_input")
        return {}
    if "code" not in frame.columns:
        errors.append(f"{source}_missing_columns:code")
        return {}

    indexed: dict[str, dict[str, object]] = {}
    all_rows: dict[str, list[dict[str, object]]] = {}
    for row in frame.to_dict(orient="records"):
        code = _valid_code(row.get("code"))
        if not code:
            errors.append(f"{source}_invalid_code:{_display(row.get('code'))}")
            continue
        all_rows.setdefault(code, []).append(row)
        if code in indexed:
            errors.append(f"{source}_duplicate_code:{code}")
            continue
        indexed[code] = row
    for code, rows in all_rows.items():
        indexed[code] = {**indexed[code], "__all_rows": rows}
    return indexed


def _load_supply_chain_rows(
    path_like: str | Path | None,
    errors: list[str],
) -> dict[str, dict[str, object]]:
    if path_like is None:
        return {}
    path = Path(path_like)
    if not path.is_file():
        errors.append(f"supply_chain_file_not_found:{path}")
        return {}
    if path.suffix.lower() != ".csv":
        errors.append(f"supply_chain_file_unreadable:not_csv:{path}")
        return {}
    try:
        frame = pd.read_csv(path, dtype=str, encoding="utf-8", keep_default_na=False)
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as exc:
        errors.append(f"supply_chain_file_unreadable:{path}:{type(exc).__name__}")
        return {}
    missing = sorted(_SUPPLY_CHAIN_COLUMNS - set(frame.columns))
    if missing:
        errors.append(f"supply_chain_file_missing_columns:{','.join(missing)}")
        return {}
    return _index_auxiliary_rows(frame, "supply_chain_file", errors)


def _choose_field(
    snapshot_row: dict[str, object],
    existing_row: dict[str, object],
    supply_row: dict[str, object],
    field: str,
    *,
    normalize_label: bool = False,
) -> tuple[str, str]:
    for row, source in (
        (snapshot_row, "snapshot"),
        (existing_row, "existing_map"),
        (supply_row, "supply_chain_file"),
    ):
        value = _text(row.get(field))
        if value:
            if normalize_label:
                value = _normalize_label(value)
            return value, source
    return "", "empty"


def _choose_text(
    snapshot_row: dict[str, object], existing_row: dict[str, object], field: str
) -> str:
    return _text(snapshot_row.get(field)) or _text(existing_row.get(field))


def _latest_updated_at(*rows: dict[str, object]) -> str:
    latest: tuple[pd.Timestamp, str] | None = None
    for row in rows:
        value = _text(row.get("updated_at"))
        if not value:
            continue
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(parsed):
            continue
        dated_value = (parsed, value)
        if latest is None or dated_value[0] > latest[0]:
            latest = dated_value
    return latest[1] if latest else ""


def _missing_codes(result: pd.DataFrame, field: str) -> list[str]:
    if result.empty:
        return []
    return result.loc[result[field].eq(""), "code"].tolist()


def _valid_code(value: object) -> str:
    text = _text(value)
    return text if _CODE_RE.fullmatch(text) else ""


def _normalize_label(value: str) -> str:
    return _LABEL_SEPARATOR_RE.sub(",", value.strip())


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "nat", "none", "null", "<na>"} else text


def _display(value: object) -> str:
    return _text(value) or "<empty>"
