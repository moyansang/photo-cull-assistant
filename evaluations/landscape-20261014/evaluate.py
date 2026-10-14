"""Offline descriptive evaluation. No model training, threshold tuning or paid API.
Human KonIQ scores are not K/D/U culling labels. Derivative identity is not
human comparison-group truth. Keep images outside the repository.
"""
import argparse,csv,hashlib,json,platform,subprocess,sys,time
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
import PIL,cv2
from PIL import Image,ImageEnhance,ImageFilter
from ai_cull_assistant.analysis_modes import assess_asset_mode
from ai_cull_assistant.grouping import assign_groups,PRESETS,GROUPING_VERSION
from ai_cull_assistant.models import PhotoAsset

p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
repo=Path(__file__).resolve().parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def asset(path,i=0):return PhotoAsset(path.stem,path,path,None,path,datetime(2000,1,1)+timedelta(days=i*731),path.suffix,preview_path=path,analysis_mode='landscape')
def predict(path,i=0):
 start=time.perf_counter(); r=assess_asset_mode(asset(path,i),mode='landscape'); e=r.focus_evidence
 return dict(recommendation=e.get('recommendation'),rejected=r.rejected,reason=r.reason,seconds=time.perf_counter()-start,face_found=r.face_found,evidence=e)
def ranks(x):
 x=np.array(x);return np.array([1+sum(x<v)+(sum(x==v)-1)/2 for v in x])
def spearman(x,y):
 if len(x)<3 or len(set(x))<2 or len(set(y))<2:return None
 return float(np.corrcoef(ranks(x),ranks(y))[0,1])
meta={r['image_name']:r for r in csv.DictReader((a.data/'metadata.csv').open())}
subset=json.loads((a.data/'scene_selection.json').read_text())
blurfile=a.data/'koniqplusplus_old.csv'
blurrows=list(csv.DictReader(blurfile.open())) if blurfile.exists() and blurfile.read_text().startswith('filename') else []
blurmeta={r['filename']:r for r in blurrows}
rows=[]
for i,s in enumerate(subset):
 path=a.data/'images'/s['name']; m=meta[s['name']];r=predict(path,i)
 assert r['reason'] != 'source_unreadable_for_analysis', path
 assert not r['face_found'] and not r['evidence'].get('visible_boxes'), path
 rows.append(dict(asset_id=s['name'],sha256=sha(path),scene_category=s['category'],scene_screening='assistant visual content screening, NOT human quality annotation',dataset_partition=m['set'],human_mos_supplied=float(m['MOS']),human_acr_mean=sum(k*float(m[f'c{k}']) for k in range(1,6)),human_votes=int(m['c_total']),human_blur_annotation=blurmeta.get(s['name']),**r))
(a.output/'predictions.json').write_text(json.dumps(rows,indent=2))
summary={'n':len(rows),'recommendations':dict(Counter(r['recommendation'] for r in rows)),'rejected':sum(r['rejected'] for r in rows),'face_boxes':sum(bool(r['evidence'].get('visible_boxes')) for r in rows),'runtime_seconds':sum(r['seconds'] for r in rows),'spearman_clear_regions_vs_human_acr':spearman([r['evidence'].get('clear_regions',0) for r in rows],[r['human_acr_mean'] for r in rows]),'human_KDU_labels':0,'culling_precision':None,'culling_recall':None,'human_group_labels':0}
summary['acr_by_recommendation']={k:{'n':sum(r['recommendation']==k for r in rows),'mean':float(np.mean([r['human_acr_mean'] for r in rows if r['recommendation']==k]))} for k in sorted(set(r['recommendation'] for r in rows))}
# Fixed development-only source-family perturbation benchmark: originals,
# byte-identical duplicate and JPEG recompression. This is a constructed
# identity proxy, never human grouping labels or an independent holdout.
variants=a.data/'variants';variants.mkdir(exist_ok=True);derivatives=[]
for i,s in enumerate(subset):
 path=a.data/'images'/s['name']
 for name in ['original','duplicate','jpeg90','blur3','crop5']:
  dest=variants/(path.stem+'__'+name+'.jpg')
  with Image.open(path) as im:
   if name in ['original','duplicate']: dest.write_bytes(path.read_bytes())
   elif name=='jpeg90':im.save(dest,quality=90)
   elif name=='blur3':im.filter(ImageFilter.GaussianBlur(3)).save(dest,quality=95)
   else:im.crop((round(im.width*.05),round(im.height*.05),round(im.width*.95),round(im.height*.95))).save(dest,quality=95)
  derivatives.append({'path':dest,'family':s['name'],'variant':name})
def metrics(items,assets):
 tp=fp=fn=tn=0;errors=[]
 for i,l in enumerate(items):
  for j in range(i):
   truth=l['family']==items[j]['family'];pred=assets[i].group_id==assets[j].group_id
   if truth and pred:tp+=1
   elif pred:fp+=1
   elif truth:fn+=1
   else:tn+=1
   if truth!=pred:errors.append([assets[i].stem,assets[j].stem,'merge' if pred else 'split'])
 return {'TP':tp,'FP':fp,'FN':fn,'TN':tn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,'errors':errors,'groups':len({x.group_id for x in assets})}
groups={}
for name,selected in [('identity_proxy',[r for r in derivatives if r['variant'] in ['original','duplicate','jpeg90']]),('perturbation_stress',derivatives)]:
 groups[name]={}
 for preset in PRESETS:
  assets=[asset(r['path'],i) for i,r in enumerate(selected)];assign_groups(assets,preset,preserve_existing=False)
  groups[name][preset]=metrics(selected,assets)
  groups[name][preset]['assignments']=[{'asset_id':x.stem,'group_id':x.group_id,'anchor':x.grouping_anchor_id} for x in assets]
# Always report derivative landscape predictions separately from real photos.
perturbations=[dict(asset_id=r['path'].name,family=r['family'],variant=r['variant'],**predict(r['path'])) for r in derivatives if r['variant'] in ['blur3','crop5']]
(a.output/'perturbations.json').write_text(json.dumps(perturbations,indent=2))
(a.output/'grouping.json').write_text(json.dumps(groups,indent=2))
summary['synthetic_perturbation_recommendations']={v:dict(Counter(r['recommendation'] for r in perturbations if r['variant']==v)) for v in ['blur3','crop5']}
summary['version']={'git':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'python':sys.version,'platform':platform.platform(),'pillow':PIL.__version__,'numpy':np.__version__,'opencv':cv2.__version__,'grouping':GROUPING_VERSION,'source_hashes':{str(p.relative_to(repo)):sha(p) for p in [repo/'src/ai_cull_assistant/analysis_modes.py',repo/'src/ai_cull_assistant/grouping.py',repo/'src/ai_cull_assistant/body_focus.py',repo/'src/ai_cull_assistant/shared_decode.py']},'metadata_sha256':sha(a.data/'metadata.csv'),'scene_selection_sha256':sha(a.data/'scene_selection.json'),'presets':{k:dict(hash_distance=v.hash_distance_threshold,structure=v.structure_threshold,aspect=v.max_aspect_ratio) for k,v in PRESETS.items()}}
assert all(sha(a.data/'images'/r['asset_id'])==r['sha256'] for r in rows), 'source changed during evaluation'
summary['original_bytes_unchanged']=True
(a.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
