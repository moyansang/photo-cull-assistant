"""SPAQ official annotation baseline. Sharpness is not a culling K/D/U label."""
import argparse,collections,hashlib,json,platform,subprocess,sys,time
from datetime import datetime
from pathlib import Path
import PIL,cv2,numpy as np
from PIL import Image
from ai_cull_assistant.analysis_modes import assess_asset_mode
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.grouping import assign_groups
p=argparse.ArgumentParser();p.add_argument('--data',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();a.output.mkdir(exist_ok=True,parents=True)
meta={r['Image name']:r for r in json.loads((a.data/'annotations.json').read_text())}
rows=[];assets=[]
for path in sorted((a.data/'images').glob('*.jpg')):
 m=meta[path.name];assert m['Landscape']>0
 sha=hashlib.sha256(path.read_bytes()).hexdigest()
 x=PhotoAsset(path.stem,path,path,None,path,datetime(2000,1,1),'.jpg',preview_path=path,analysis_mode='landscape');assets.append(x)
 t=time.perf_counter();r=assess_asset_mode(x,mode='landscape'); elapsed=time.perf_counter()-t
 with Image.open(path) as im: size=list(im.size)
 assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
 rows.append({'name':path.name,'sha256':sha,'size':size,'official_annotations':m,'local_recommendation':r.focus_evidence.get('recommendation'),'rejected':r.rejected,'reason':r.reason,'seconds':elapsed,'evidence':r.focus_evidence})
 print(path.name,r.focus_evidence.get('recommendation'),m['Sharpness'],flush=True)
(a.output/'predictions.json').write_text(json.dumps(rows,indent=2))
def ranks(x):return np.array([1+sum(v<w for v in x)+(sum(v==w for v in x)-1)/2 for w in x])
def rho(x,y):
 if len(x)<3 or len(set(x))<2 or len(set(y))<2:return None
 return float(np.corrcoef(ranks(x),ranks(y))[0,1])
sharp=[r['official_annotations']['Sharpness'] for r in rows];clear=[r['evidence'].get('clear_regions',0) for r in rows]
all_land=[m for m in meta.values() if m['Landscape']>0]
q=np.quantile([m['Sharpness'] for m in all_land],[.25,.5,.75]).tolist()
summary={'n':len(rows),'official_landscape_records':len(all_land),'sampling':'all Landscape>0 in first bounded gzip prefix; convenience archive-order sample, no quality filtering','recommendations':dict(collections.Counter(r['local_recommendation'] for r in rows)),'rejects':sum(r['rejected'] for r in rows),'unreadable':sum(r['reason']=='source_unreadable_for_analysis' for r in rows),'visible_boxes':sum(bool(r['evidence'].get('visible_boxes')) for r in rows),'sharpness_range':[min(sharp),max(sharp)] if sharp else None,'full_landscape_sharpness_quartiles':q,'spearman_clear_regions_vs_human_sharpness':rho(clear,sharp),'human_KDU_labels':0,'human_comparison_group_labels':0,'culling_accuracy':None,'runtime_seconds':sum(r['seconds'] for r in rows),'original_bytes_unchanged':True}
summary['sharpness_strata']={str(i):{'lower':lo,'upper':hi,'n':len(s),'recommendations':dict(collections.Counter(r['local_recommendation'] for r in s))} for i,(lo,hi,s) in enumerate([(lo,hi,[r for r in rows if lo<=r['official_annotations']['Sharpness']<hi]) for lo,hi in zip([-float('inf')]+q,q+[float('inf')])],1)}
# JSON allows no nonfinite bounds: make outer endpoints explicit nulls.
summary['sharpness_strata']['1']['lower']=None;summary['sharpness_strata']['4']['upper']=None
summary['grouping']={}
for preset in ['standard','strict','loose']:
 assign_groups(assets,preset,preserve_existing=False)
 groups=collections.defaultdict(list)
 for x in assets:groups[str(x.group_id)].append(x.stem)
 summary['grouping'][preset]={'group_count':len(groups),'members':dict(groups),'accuracy':None}
repo=Path(__file__).resolve().parents[2]
summary['versions']={'git':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'python':sys.version,'pillow':PIL.__version__,'numpy':np.__version__,'opencv':cv2.__version__,'platform':platform.platform(),'source_hashes':{name:hashlib.sha256((repo/'src/ai_cull_assistant'/name).read_bytes()).hexdigest() for name in ['analysis_modes.py','grouping.py','body_focus.py','shared_decode.py']},'annotations_sha256':hashlib.sha256((a.data/'annotations.json').read_bytes()).hexdigest()}
(a.output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False));print(json.dumps(summary,indent=2,allow_nan=False))
