"""Post-hoc development probe; ratio thresholds are NOT calibrated accept gates.
Filtering only changes keep to review, never creates a reject.
"""
from pathlib import Path
import argparse,json,time,hashlib
from collections import Counter
import numpy as np
from PIL import Image,ImageOps,ImageFilter
from ai_cull_assistant.analysis_modes import landscape_evidence
parser=argparse.ArgumentParser();parser.add_argument('--koniq-data',type=Path,required=True);parser.add_argument('--spaq-data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
k=args.koniq_data;s=args.spaq_data;p=args.output;p.mkdir(parents=True,exist_ok=True)
def gates(e):
 clear=[r for r in e['regions'] if r['state']=='clear'];ratios=[r['coarse_laplacian_normalized']/max(r['laplacian_normalized'],1e-12) for r in clear]
 return {'baseline':e['recommendation'],'clear_regions':len(clear),'clear_ratios':ratios,'ratio_candidates':{str(v):'keep' if sum(r>=v for r in ratios)>=2 else 'review' for v in [.05,.1,.2]}}
def openim(path):
 with Image.open(path) as im:return ImageOps.exif_transpose(im).convert('RGB')
original=[]
inputs=[('spaq_'+f.name,f) for f in sorted((s/'images').glob('*.jpg'))]+[('koniq_'+r['name'],k/'images'/r['name']) for r in json.loads((k/'scene_selection.json').read_text())]
for n,f in inputs:
 with openim(f) as im:original.append({'source':n,**gates(landscape_evidence(im))})
noise=[]
for name,path in [('spaq_08862',s/'images/08862.jpg'),('koniq_field_blur3',k/'variants/169779095__blur3.jpg')]:
 with openim(path) as im:
  rgb=np.asarray(im).astype(np.float32)
  for sigma in [0,4,8,16]:
   for seed in range(20261014,20261019):
    rng=np.random.default_rng(seed);arr=rgb if sigma==0 else np.clip(rgb+rng.normal(0,sigma,rgb.shape).astype(np.float32),0,255)
    noisy=Image.fromarray(arr.astype(np.uint8));e=landscape_evidence(noisy)
    noise.append({'source':name,'sigma_8bit':sigma,'seed':seed,**gates(e),'prefilter_sigma1':landscape_evidence(noisy.filter(ImageFilter.GaussianBlur(1)))['recommendation']})
# Additional all-clear synthetic high-frequency stripes/noise: no human labels.
yy,xx=np.indices((512,512));arr=np.full((512,512,3),128,np.uint8);arr[:128,:256]=(((xx[:128,:256]//4+yy[:128,:256]//4)%2)*255).astype(np.uint8)[:,:,None]
control=gates(landscape_evidence(Image.fromarray(arr)))
summary={'originals':{'baseline':dict(Counter(r['baseline'] for r in original)),**{str(v):dict(Counter(r['ratio_candidates'][str(v)] for r in original)) for v in [.05,.1,.2]}},'noise':{n:{str(sig):{'baseline_keep':sum(r['baseline']=='keep' for r in noise if r['source']==n and r['sigma_8bit']==sig),**{f'ratio{v}_keep':sum(r['ratio_candidates'][str(v)]=='keep' for r in noise if r['source']==n and r['sigma_8bit']==sig) for v in [.05,.1,.2]},'prefilter1_keep':sum(r['prefilter_sigma1']=='keep' for r in noise if r['source']==n and r['sigma_8bit']==sig)} for sig in [0,4,8,16]} for n in ['spaq_08862','koniq_field_blur3']},'two_tile_control':control}
(p/'detail-persistence.json').write_text(json.dumps({'summary':summary,'originals':original,'noise':noise},indent=2));print(json.dumps(summary,indent=2))
