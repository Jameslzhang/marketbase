import pandas as pd, glob, os
runs = sorted(glob.glob('data/daily_runs/2026-07-29/*postclose*'), key=os.path.getmtime)
latest = runs[-1]
df = pd.read_csv(os.path.join(latest, 'market_snapshot.csv'))
no_ind = df[df['industry'].isna() | df['industry'].astype(str).str.strip().isin({'', 'nan', 'None', '<NA>'})]
print('Missing industry:', len(no_ind), '/', len(df))
no_conc = df[df['concepts'].isna() | df['concepts'].astype(str).str.strip().isin({'', 'nan', 'None', '<NA>'})]
print('Missing concepts:', len(no_conc), '/', len(df))
print('Markets of missing:', no_ind['market'].value_counts().to_dict())