"""Check classification coverage."""
import pandas as pd

# Check classification_source.csv
src = pd.read_csv(
    r"d:\Environment\marketbase\data\daily_runs\classification_source.csv",
    dtype=str, keep_default_na=False, comment="#",
)
print("=== classification_source.csv ===")
print(f"Total rows: {len(src)}")
print(f"Industry filled: {(src['industry'] != '').sum()} / {len(src)}")
print(f"Concepts filled: {(src['concepts'] != '').sum()} / {len(src)}")
print()

# Check latest classification_map.csv
cmap = pd.read_csv(
    r"d:\Environment\marketbase\data\daily_runs\2026-07-29\150957_postclose_objective_data\classification_map.csv",
    dtype=str, keep_default_na=False,
)
print("=== latest classification_map.csv ===")
print(f"Total rows: {len(cmap)}")
industry_filled = (cmap["industry"] != "").sum()
concepts_filled = (cmap["concepts"] != "").sum()
print(f"Industry filled: {industry_filled} / {len(cmap)} ({industry_filled / len(cmap) * 100:.1f}%)")
print(f"Concepts filled: {concepts_filled} / {len(cmap)} ({concepts_filled / len(cmap) * 100:.1f}%)")
print()

# Check source distribution
print("Industry source distribution:")
print(cmap["industry_source"].value_counts().to_string())
print()
print("Concepts source distribution:")
print(cmap["concepts_source"].value_counts().to_string())
print()

# Check market snapshot coverage
try:
    snap = pd.read_csv(
        r"d:\Environment\marketbase\data\daily_runs\2026-07-29\150957_postclose_objective_data\market_snapshot.csv",
        dtype=str, keep_default_na=False,
    )
    print("=== market_snapshot.csv ===")
    print(f"Total rows: {len(snap)}")
    ind_filled = (snap["industry"] != "").sum()
    con_filled = (snap["concepts"] != "").sum()
    print(f"Industry filled: {ind_filled} / {len(snap)} ({ind_filled / len(snap) * 100:.1f}%)")
    print(f"Concepts filled: {con_filled} / {len(snap)} ({con_filled / len(snap) * 100:.1f}%)")
except Exception as e:
    print(f"market_snapshot.csv error: {e}")