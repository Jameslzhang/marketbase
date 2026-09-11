from datetime import datetime, timezone

import pandas as pd

import marketbase.volume_ratio as volume_ratio


def test_compute_volume_ratios_batch_reads_each_daily_average_once(monkeypatch, tmp_path):
    calls = []

    def fake_avg(code, cache_root, *, observed_at=None):
        calls.append(code)
        return 100.0

    monkeypatch.setattr(volume_ratio, "_avg_5d_volume", fake_avg)
    frame = pd.DataFrame(
        [
            {"code": "000001", "volume": 100.0},
            {"code": "000001", "volume": 120.0},
            {"code": "000002", "volume": 80.0},
        ]
    )

    result = volume_ratio.compute_volume_ratios_batch(
        frame, tmp_path, datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    )

    assert calls == ["000001", "000002"]
    assert result["volume_ratio"].tolist() == [1.0, 1.2, 0.8]
