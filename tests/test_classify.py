from pathlib import Path

import pandas as pd

from marketbase.classify import build_classification_map


OUTPUT_COLUMNS = [
    "code",
    "name",
    "industry",
    "concepts",
    "supply_chain",
    "industry_source",
    "concepts_source",
    "supply_chain_source",
    "source",
    "updated_at",
    "coverage_status",
]


def test_snapshot_values_have_priority_and_existing_map_only_fills_empty_values():
    snapshot = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "快照名称",
                "industry": "快照行业",
                "concepts": "快照概念，概念二",
                "supply_chain": "",
                "updated_at": "2026-07-21T10:00:00",
            }
        ]
    )
    existing = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "旧名称",
                "industry": "旧行业",
                "concepts": "旧概念",
                "supply_chain": "上游;中游",
                "updated_at": "2026-07-20T10:00:00",
            }
        ]
    )

    result, audit = build_classification_map(snapshot, existing_map=existing)

    assert list(result.columns) == OUTPUT_COLUMNS
    assert result.to_dict("records") == [
        {
            "code": "000001",
            "name": "快照名称",
            "industry": "快照行业",
            "concepts": "快照概念,概念二",
            "supply_chain": "上游,中游",
            "industry_source": "snapshot",
            "concepts_source": "snapshot",
            "supply_chain_source": "existing_map",
            "source": "eastmoney",
            "updated_at": "2026-07-21T10:00:00",
            "coverage_status": "covered",
        }
    ]
    assert audit["industry_coverage_count"] == 1
    assert audit["concepts_coverage_count"] == 1
    assert audit["supply_chain_coverage_count"] == 1


def test_explicit_csv_fills_supply_chain_without_inference(tmp_path: Path):
    path = tmp_path / "supply_chain.csv"
    path.write_text(
        "code,industry,concepts,supply_chain\n"
        "000001,文件行业,文件概念,上游， 中游;下游\n",
        encoding="utf-8",
    )
    snapshot = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "涨停半导体医疗石油",
                "industry": "",
                "concepts": "",
                "change_pct": 9.9,
            }
        ]
    )

    result, audit = build_classification_map(snapshot, supply_chain_path=path)

    row = result.iloc[0]
    assert row["industry"] == "文件行业"
    assert row["concepts"] == "文件概念"
    assert row["supply_chain"] == "上游,中游,下游"
    assert row["industry_source"] == "supply_chain_file"
    assert row["concepts_source"] == "supply_chain_file"
    assert row["supply_chain_source"] == "supply_chain_file"
    assert audit["errors"] == []


def test_empty_values_have_empty_sources_and_latest_objective_update_time():
    snapshot = pd.DataFrame(
        [
            {"code": "000001", "name": "甲", "updated_at": "2026-07-20"},
            {"code": "000002", "name": "乙", "industry": "行业", "updated_at": ""},
        ]
    )

    result, audit = build_classification_map(snapshot)

    assert result.loc[0, "industry_source"] == "empty"
    assert result.loc[0, "concepts_source"] == "empty"
    assert result.loc[0, "supply_chain_source"] == "empty"
    assert result.loc[1, "industry_source"] == "snapshot"
    assert result.loc[0, "updated_at"] == "2026-07-20"
    assert result.loc[1, "updated_at"] == ""
    assert audit["missing_industry_codes"] == ["000001"]
    assert audit["missing_concepts_codes"] == ["000001", "000002"]
    assert audit["missing_supply_chain_codes"] == ["000001", "000002"]


def test_duplicate_and_invalid_snapshot_codes_are_audited_but_output_is_stable():
    snapshot = pd.DataFrame(
        [
            {"code": "000002", "name": "先出现"},
            {"code": "bad", "name": "非法"},
            {"code": "000002", "name": "重复"},
            {"code": "12345", "name": "位数错误"},
            {"code": "000001", "name": "后出现"},
        ]
    )

    result, audit = build_classification_map(snapshot)

    assert result["code"].tolist() == ["000002", "000001"]
    assert result.loc[0, "name"] == "先出现"
    assert audit["total_snapshot_rows"] == 5
    assert audit["output_rows"] == 2
    assert audit["unique_code_count"] == 2
    assert audit["duplicate_code_count"] == 1
    assert audit["invalid_code_count"] == 2
    assert len(audit["errors"]) == 3


def test_supply_chain_file_errors_are_isolated_from_snapshot_results(tmp_path: Path):
    missing = tmp_path / "missing.csv"
    snapshot = pd.DataFrame([{"code": "000001", "name": "甲", "industry": "事实行业"}])

    result, audit = build_classification_map(snapshot, supply_chain_path=missing)

    assert result.loc[0, "industry"] == "事实行业"
    assert result.loc[0, "industry_source"] == "snapshot"
    assert result.loc[0, "supply_chain"] == ""
    assert any("not_found" in error for error in audit["errors"])

    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("code,industry\n000001,文件行业\n", encoding="utf-8")
    result, audit = build_classification_map(snapshot, supply_chain_path=incomplete)
    assert result.loc[0, "industry"] == "事实行业"
    assert result.loc[0, "supply_chain"] == ""
    assert any("missing_columns" in error for error in audit["errors"])


def test_supply_chain_file_invalid_and_duplicate_records_are_isolated(tmp_path: Path):
    path = tmp_path / "supply_chain.csv"
    path.write_text(
        "code,industry,concepts,supply_chain\n"
        "000001,文件行业,文件概念,上游\n"
        "bad,不应进入,不应进入,不应进入\n"
        "000001,重复行业,重复概念,重复链条\n",
        encoding="utf-8",
    )
    snapshot = pd.DataFrame([{"code": "000001", "name": "甲"}])

    result, audit = build_classification_map(snapshot, supply_chain_path=path)

    assert result.loc[0, "industry"] == "文件行业"
    assert result.loc[0, "supply_chain"] == "上游"
    assert any("supply_chain_file_invalid_code" in error for error in audit["errors"])
    assert any("supply_chain_file_duplicate_code" in error for error in audit["errors"])


def test_output_does_not_propagate_strategy_fields_or_text_and_does_not_mutate_inputs(tmp_path: Path):
    snapshot = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "甲",
                "industry": "行业",
                "concepts": "概念",
                "score": 99,
                "signal": "buy",
                "strategy_note": "recommend this candidate",
            }
        ]
    )
    before = snapshot.copy(deep=True)

    result, _ = build_classification_map(snapshot)

    assert list(result.columns) == OUTPUT_COLUMNS
    assert not any(
        any(token in str(value).lower() for token in ("score", "rank", "signal", "candidate", "recommend", "buy", "sell", "probability", "mainline", "tier"))
        for value in result.to_numpy().ravel()
    )
    pd.testing.assert_frame_equal(snapshot, before)
