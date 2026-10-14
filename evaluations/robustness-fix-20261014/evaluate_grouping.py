"""Compare committed baseline with current production crop fallback, offline."""
from pathlib import Path
from datetime import datetime,timedelta
import argparse,collections,hashlib,importlib.util,itertools,json,subprocess,sys,tempfile,time
from PIL import Image,ImageOps
from ai_cull_assistant import grouping as new
from ai_cull_assistant.models import PhotoAsset
p=argparse.ArgumentParser();p.add_argument('--koniq-data',type=Path,required=True);p.add_argument('--spaq-data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=True,parents=True)
repo=Path(__file__).resolve().parents[2]
base_commit='70f73a99b1902f37a135701898a462feac68df80'
with tempfile.TemporaryDirectory(prefix='cull-crop-compare-') as temporary:
 t=Path(temporary);source=subprocess.check_output(['git','show',base_commit+':src/ai_cull_assistant/grouping.py'],cwd=repo);(t/'baseline.py').write_bytes(source)
 spec=importlib.util.spec_from_file_location('ai_cull_assistant.grouping_baseline',t/'baseline.py');old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
 inputs=[('koniq_'+r['name'],a.koniq_data/'images'/r['name']) for r in json.loads((a.koniq_data/'scene_selection.json').read_text())]+[('spaq_'+f.name,f) for f in sorted((a.spaq_data/'images').glob('*.jpg'))]
 original_hashes={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in inputs};features={};variants={};times={'baseline':0,'candidate':0};paths=[]
 fractions=[.01,.02,.03,.05,.07,.1]
 def make_asset(path,i=0):return PhotoAsset(path.stem,path,path,None,path,datetime(2000,1,1)+timedelta(days=i*730),path.suffix,preview_path=path,analysis_mode='landscape')
 placeholder=make_asset(Path('placeholder.png'))
 def compare(left,right):
  cfg=new.PRESETS['standard'];stats=new.GroupingStats();baseline=new._similar(placeholder,placeholder,left,right,cfg,stats);fallback=False if baseline else new._crop_aligned_similar(placeholder,placeholder,left,right,cfg,stats)
  return {'baseline':baseline,'candidate':baseline or fallback,'fallback':fallback}
 for n,path in inputs:
  with Image.open(path) as src:im=ImageOps.exif_transpose(src).convert('RGB')
  start=time.perf_counter();old._visual_from_image(im);times['baseline']+=time.perf_counter()-start
  start=time.perf_counter();features[n]=new._visual_from_image(im);times['candidate']+=time.perf_counter()-start
  variants[n]={}
  for f in fractions:
   c=im.crop((round(im.width*f),round(im.height*f),round(im.width*(1-f)),round(im.height*(1-f))));variants[n][f]=new._visual_from_image(c)
  # Production path end-to-end: source plus 2.5% and 5% JPEG crop derivatives.
  op=t/(n[:-4]+'__0original.jpg');op.write_bytes(path.read_bytes());paths.append((op,n))
  for label,f in [('1crop25',.025),('2crop50',.05)]:
   c=im.crop((round(im.width*f),round(im.height*f),round(im.width*(1-f)),round(im.height*(1-f))));cp=t/(n[:-4]+'__'+label+'.jpg');c.save(cp,quality=95);paths.append((cp,n))
  im.close()
 positive=[{'source':n,'crop':f,**compare(features[n],v)} for n in features for f,v in variants[n].items()]
 negative=[]
 for n,o in itertools.combinations(features,2):negative.append({'source':n,'other':o,'crop':0,**compare(features[n],features[o])})
 for n in features:
  for o in features:
   if n!=o:
    for f,v in variants[o].items():negative.append({'source':n,'other':o,'crop':f,**compare(features[n],v)})
 e2e={}
 for label,module in [('baseline',old),('candidate',new)]:
  assets=[make_asset(path,i) for i,(path,n) in enumerate(paths)];stats=module.GroupingStats();start=time.perf_counter();module.assign_groups(assets,stats=stats);elapsed=time.perf_counter()-start
  groups=collections.defaultdict(list)
  for x,(_,family) in zip(assets,paths):groups[x.group_id].append({'name':x.stem,'family':family,'anchor':x.grouping_anchor_id})
  e2e[label]={'seconds':elapsed,'groups':dict(groups),'group_count':len(groups),'cross_source_groups':sum(len({r['family'] for r in v})>1 for v in groups.values()),'hash_candidates':stats.hash_candidates,'structure_comparisons':stats.structure_comparisons,'crop_candidates':getattr(stats,'crop_candidates',0),'crop_matches':getattr(stats,'crop_matches',0)}
 summary={'baseline_commit':base_commit,'source_count':len(inputs),'synthetic_positive_pair_count':len(positive),'different_source_proxy_pair_count':len(negative),'positive_matches':{label:sum(r[label] for r in positive) for label in ['baseline','candidate']},'by_crop':{str(f):{label:sum(r[label] for r in positive if r['crop']==f) for label in ['baseline','candidate']} for f in fractions},'different_source_matches':{label:sum(r[label] for r in negative) for label in ['baseline','candidate']},'feature_seconds':times,'end_to_end':e2e,'source_sha256':original_hashes,'production_grouping_sha256':hashlib.sha256((repo/'src/ai_cull_assistant/grouping.py').read_bytes()).hexdigest()}
 assert all(hashlib.sha256(p.read_bytes()).hexdigest()==original_hashes[n] for n,p in inputs)
 (a.output/'grouping-comparison.json').write_text(json.dumps({'summary':summary,'positive':positive,'unexpected_proxy_matches':[r for r in negative if r['candidate']]},indent=2));print(json.dumps({k:v for k,v in summary.items() if k not in ['source_sha256','end_to_end']},indent=2));print('e2e',[(k,{f:v[f] for f in ['seconds','group_count','cross_source_groups','crop_matches']}) for k,v in e2e.items()])
