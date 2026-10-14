"""Separate spatial coverage from pixel-scale effects using real region metric."""
import argparse,json,time
from pathlib import Path
from collections import Counter
import numpy as np,cv2
from PIL import Image,ImageOps
from ai_cull_assistant.analysis_modes import landscape_evidence
from ai_cull_assistant.body_focus import _region_metrics
parser=argparse.ArgumentParser();parser.add_argument('--spaq-data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
base=args.spaq_data/'images';out=args.output;out.mkdir(parents=True,exist_ok=True);rows=[]
def measure(arr):
 bgr=cv2.cvtColor(arr,cv2.COLOR_RGB2BGR);h,w=bgr.shape[:2]
 return _region_metrics(bgr,np.full((h,w),255,np.uint8),(0,0,w,h))
for p in sorted(base.glob('*.jpg')):
 with Image.open(p) as source:im=ImageOps.exif_transpose(source).convert('RGB')
 production=landscape_evidence(im);rgb=np.asarray(im);h,w=rgb.shape[:2];modes={k:[] for k in ['native_centres_replay','native_full_tiles','same_centres_half_scale']};start=time.perf_counter()
 for row in range(4):
  for col in range(4):
   tile=rgb[round(row*h/4):round((row+1)*h/4),round(col*w/4):round((col+1)*w/4)];th,tw=tile.shape[:2]
   centre=tile[max(0,(th-384)//2):min(th,(th+384)//2),max(0,(tw-384)//2):min(tw,(tw+384)//2)]
   modes['native_centres_replay'].append(measure(centre));modes['native_full_tiles'].append(measure(tile));modes['same_centres_half_scale'].append(measure(cv2.resize(centre,None,fx=.5,fy=.5,interpolation=cv2.INTER_AREA)))
 results={mode:{'clear_regions':sum(r['state']=='clear' for r in regs),'states':dict(Counter(r['state'] for r in regs)),'recommendation':'keep' if sum(r['state']=='clear' for r in regs)>=2 else 'review'} for mode,regs in modes.items()}
 assert results['native_centres_replay']['clear_regions']==production['clear_regions']
 assert [r['state'] for r in modes['native_centres_replay']]==[r['state'] for r in production['regions']]
 rows.append({'name':p.name,'results':results,'combined_probe_seconds':time.perf_counter()-start});im.close()
(out/'coverage-scale.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
