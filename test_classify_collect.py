"""Test classification collector."""
import tempfile, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from marketbase.classification_collector import collect_classification

d = tempfile.mkdtemp()
p = os.path.join(d, 'test_classification.csv')
print(f"Output: {p}")
df = collect_classification(p)
print(f"Rows: {len(df)}")
print(df.head(3).to_string())
print("---")
print(f"Industry coverage: {(df['industry']!='').sum()}/{len(df)}")
print(f"Concepts coverage: {(df['concepts']!='').sum()}/{len(df)}")
print(f"Unique industries: {df['industry'].nunique()}")
print("Done!")