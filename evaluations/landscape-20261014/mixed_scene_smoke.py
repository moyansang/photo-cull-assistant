"""Supplemental mixed-content test; do NOT call these 60 images landscapes."""
import csv,json,sys,hashlib
from collections import Counter
from pathlib import Path
from datetime import datetime
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.analysis_modes import assess_asset_mode
base=Path(sys.argv[1]);out=Path(sys.argv[2]);meta={r['image_name']:r for r in csv.DictReader((base/'metadata.csv').open())};rows=[]
for p in sorted((base/'images').glob('*.jpg')):
 x=PhotoAsset(p.stem,p,p,None,p,datetime(2000,1,1),p.suffix,preview_path=p,analysis_mode='landscape')
 r=assess_asset_mode(x,mode='landscape'); e=r.focus_evidence;m=meta.get(p.name)
 assert r.reason!='source_unreadable_for_analysis'
 rows.append({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'recommendation':e['recommendation'],'rejected':r.rejected,'clear_regions':e['clear_regions'],'human_acr_mean':sum(k*float(m[f'c{k}']) for k in range(1,6)) if m else None,'human_votes':int(m['c_total']) if m else None})
report={'scope':'60 mixed-content photos, not a landscape benchmark','n':len(rows),'recommendations':dict(Counter(r['recommendation'] for r in rows)),'rows':rows}
report['missing_human_label_names']=[r['name'] for r in rows if r['human_acr_mean'] is None]
report['exploratory_acr_bins']={k:{'n':len(s),'recommendations':dict(Counter(r['recommendation'] for r in s))} for k,s in [('below2',[r for r in rows if r['human_acr_mean'] is not None and r['human_acr_mean']<2]),('2to3',[r for r in rows if r['human_acr_mean'] is not None and 2<=r['human_acr_mean']<3]),('3plus',[r for r in rows if r['human_acr_mean'] is not None and r['human_acr_mean']>=3])]}
out.write_text(json.dumps(report,indent=2));print({k:v for k,v in report.items() if k!='rows'})
