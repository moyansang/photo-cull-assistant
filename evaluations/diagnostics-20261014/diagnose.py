"""Read-only attribution experiments; all variants in memory, no production edits.
Proxy same-source/different-source pairs are NOT human comparison-group labels.
Sharpness ranks are NOT technical-discard K/D/U truth.
"""
from pathlib import Path
from collections import Counter,defaultdict
import argparse,hashlib,io,itertools,json,platform,subprocess,time,sys
import numpy as np
from PIL import Image,ImageOps,ImageFilter
from ai_cull_assistant.analysis_modes import landscape_evidence
from ai_cull_assistant.grouping import _visual_from_image,grayscale_structure_similarity,PRESETS
root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser();parser.add_argument('--koniq-data',type=Path,required=True);parser.add_argument('--spaq-data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
k=args.koniq_data;s=args.spaq_data;out=args.output;out.mkdir(parents=True,exist_ok=True)
spaq=json.loads((s/'annotations.json').read_text());sm={r['Image name']:r for r in spaq};ks=json.loads((k/'scene_selection.json').read_text())
inputs=[('koniq_'+r['name'],k/'images'/r['name']) for r in ks]+[('spaq_'+p.name,p) for p in sorted((s/'images').glob('*.jpg'))]
source_hashes={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in inputs}
def oriented(p):
 with Image.open(p) as im:return ImageOps.exif_transpose(im).convert('RGB')
def crop(im,f):return im.crop((round(im.width*f),round(im.height*f),round(im.width*(1-f)),round(im.height*(1-f))))
def pair(a,b,hd=10,st=.78):
 d=(a.whole^b.whole).bit_count();structure=grayscale_structure_similarity(a,b);aspect=max(a.aspect,b.aspect)/min(a.aspect,b.aspect)
 return {'hash_distance':d,'structure':structure,'aspect_ratio':aspect,'hash_pass':d<=hd,'structure_pass':structure+1e-6>=st,'aspect_pass':aspect<=1.15,'match':d<=hd and structure+1e-6>=st and aspect<=1.15}
# Fixed diagnostic alternatives; no search for an optimal threshold.
bank_fractions=[0,.025,.05,.1]
probe_fractions=[.01,.02,.03,.05,.07,.1]
features={};banks={};variants={};probe=[];timings={}
for name,path in inputs:
 with oriented(path) as im:
  t=time.perf_counter();features[name]=_visual_from_image(im);timings[name]={'single_feature_seconds':time.perf_counter()-t}
  t=time.perf_counter();banks[name]=[_visual_from_image(crop(im,f)) for f in bank_fractions];timings[name]['bank_feature_seconds']=time.perf_counter()-t
  variants[name]={f:_visual_from_image(crop(im,f)) for f in probe_fractions}
  for f,v in variants[name].items():probe.append({'source':name,'crop_each_edge':f,**pair(features[name],v)})
def candidates(name,v):
 baseline=pair(features[name],v)
 relaxed_hash=pair(features[name],v,20,.78)
 relaxed_both=pair(features[name],v,20,.5)
 comparisons=[pair(b,v) for b in banks[name]]
 return {'baseline':baseline['match'],'hash20_only':relaxed_hash['match'],'hash20_structure05':relaxed_both['match'],'center_crop_bank':any(r['match'] for r in comparisons),'bank_winner':next((i for i,r in enumerate(comparisons) if r['match']),None)}
positive=[{'source':name,'crop_each_edge':f,**candidates(name,v)} for name in variants for f,v in variants[name].items()]
# Full diagnostic pair sets: all original cross-source unordered pairs,
# and every original against every other source's crop at all six levels.
negative=[]
for name,other in itertools.combinations(features,2):negative.append({'representative':name,'query_source':other,'crop':0,**candidates(name,features[other])})
for name in features:
 for other in features:
  if name!=other:
   for f,v in variants[other].items():negative.append({'representative':name,'query_source':other,'crop':f,**candidates(name,v)})
methods=['baseline','hash20_only','hash20_structure05','center_crop_bank']
group_summary={'source_count':len(inputs),'positive_pairs':len(positive),'different_source_proxy_pairs':len(negative),'bank_fractions':bank_fractions,'probe_fractions':probe_fractions,'results':{m:{'same_source_recovered':sum(r[m] for r in positive),'different_source_matches':sum(r[m] for r in negative),'by_crop':{str(f):sum(r[m] for r in positive if r['crop_each_edge']==f) for f in probe_fractions}} for m in methods},'feature_times':timings}
(out/'grouping-attribution.json').write_text(json.dumps({'summary':group_summary,'baseline_pair_details':probe,'candidate_positive_pairs':positive,'different_source_matches':{m:[r for r in negative if r[m]] for m in methods}},indent=2))
# Native sampling coverage, resolution changes and pre-smoothing.
score_rows=[]
for path in sorted((s/'images').glob('*.jpg')):
 with oriented(path) as im:
  variants_images=[('native',im),('half',im.resize((im.width//2,im.height//2),Image.Resampling.LANCZOS)),('quarter',im.resize((im.width//4,im.height//4),Image.Resampling.LANCZOS)),('prefilter_sigma05',im.filter(ImageFilter.GaussianBlur(.5))),('prefilter_sigma10',im.filter(ImageFilter.GaussianBlur(1)))]
  for mode,image in variants_images:
   t=time.perf_counter();e=landscape_evidence(image)
   coverage=sum(r['native_size'][0]*r['native_size'][1] for r in e['regions'])/(image.width*image.height)
   score_rows.append({'name':path.name,'transform':mode,'human_sharpness_original_only':sm[path.name]['Sharpness'],'size':[image.width,image.height],'recommendation':e['recommendation'],'clear_regions':e['clear_regions'],'textured_regions':e['textured_regions'],'sampled_pixel_fraction':coverage,'seconds':time.perf_counter()-t,'region_states':dict(Counter(r['state'] for r in e['regions'])),'regions':e['regions']})
   if image is not im:image.close()
# Synthetic counterfactual: add zero-mean Gaussian pixel noise to a fixed
# software-review input. Human score is NOT copied onto altered images.
noise=[]
for name,path in [('spaq_08862',s/'images/08862.jpg'),('koniq_field_blur3',k/'variants/169779095__blur3.jpg')]:
 with oriented(path) as im:
  rgb=np.asarray(im).astype(np.float32)
  for sigma in [0,1,2,4,8,16]:
   rng=np.random.default_rng(20261014)
   arr=rgb if sigma==0 else np.clip(rgb+rng.normal(0,sigma,rgb.shape).astype(np.float32),0,255)
   noisy=Image.fromarray(arr.astype(np.uint8));e=landscape_evidence(noisy)
   pref=landscape_evidence(noisy.filter(ImageFilter.GaussianBlur(.5)))
   noise.append({'source':name,'sigma_8bit':sigma,'recommendation':e['recommendation'],'clear_regions':e['clear_regions'],'prefilter05_recommendation':pref['recommendation'],'prefilter05_clear_regions':pref['clear_regions']})
# Design tradeoff control: exactly two sharp tiles and a low-texture background.
arr=np.full((512,512,3),128,np.uint8);yy,xx=np.indices((128,256));arr[:128,:256]=(((xx//4+yy//4)%2)*255).astype(np.uint8)[:,:,None]
control=landscape_evidence(Image.fromarray(arr))
summary={'n':9,'full_spaq_landscape_sharpness_q1':39.9225,'transforms':{mode:{'recommendations':dict(Counter(r['recommendation'] for r in score_rows if r['transform']==mode)),'low_sharpness_q1_recommendations':dict(Counter(r['recommendation'] for r in score_rows if r['transform']==mode and r['human_sharpness_original_only']<39.9225))} for mode in ['native','half','quarter','prefilter_sigma05','prefilter_sigma10']},'threshold_only_candidates':{str(n):{'keep':sum(r['clear_regions']>=n for r in score_rows if r['transform']=='native'),'low_sharpness_q1_keep':sum(r['clear_regions']>=n for r in score_rows if r['transform']=='native' and r['human_sharpness_original_only']<39.9225)} for n in [2,4,8]},'two_sharp_tile_control':{'current_recommendation':control['recommendation'],'clear_regions':control['clear_regions'],'min8_candidate':'keep' if control['clear_regions']>=8 else 'review'},'noise_counterfactuals':noise}
(out/'landscape-attribution.json').write_text(json.dumps({'summary':summary,'rows':score_rows},indent=2))
assert all(hashlib.sha256(p.read_bytes()).hexdigest()==source_hashes[name] for name,p in inputs)
version={'git':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'python':sys.version,'source_hashes':{name:hashlib.sha256((root/'src/ai_cull_assistant'/name).read_bytes()).hexdigest() for name in ['analysis_modes.py','body_focus.py','grouping.py']},'input_sha256':source_hashes,'original_bytes_unchanged':True}
(out/'version.json').write_text(json.dumps(version,indent=2))
print(json.dumps({'grouping':group_summary,'landscape':summary},indent=2))
