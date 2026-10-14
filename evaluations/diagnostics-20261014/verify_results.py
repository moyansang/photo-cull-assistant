"""Validate saved experiment accounting and baseline helper equivalence."""
import argparse,hashlib,json
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageOps
from ai_cull_assistant.grouping import _similar,_visual_from_image,grayscale_structure_similarity,GroupingStats,PRESETS
p=argparse.ArgumentParser();p.add_argument('--koniq-data',type=Path,required=True);p.add_argument('--spaq-data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output
j=json.loads((out/'grouping-attribution.json').read_text());assert len(j['baseline_pair_details'])==84
by_source={}
for r in j['baseline_pair_details']:by_source.setdefault(r['source'],[]).append(r)
count=0
for name,rows in by_source.items():
 folder=a.koniq_data if name.startswith('koniq_') else a.spaq_data;fname=name.split('_',1)[1]
 with Image.open(folder/'images'/fname) as src:im=ImageOps.exif_transpose(src).convert('RGB')
 left=_visual_from_image(im)
 for r in rows:
  f=r['crop_each_edge'];c=im.crop((round(im.width*f),round(im.height*f),round(im.width*(1-f)),round(im.height*(1-f))));right=_visual_from_image(c)
  v=SimpleNamespace(analysis_mode='landscape')
  assert _similar(v,v,left,right,PRESETS['standard'],GroupingStats())==r['match']
  assert abs(grayscale_structure_similarity(left,right)-r['structure'])<1e-8
  count+=1
 im.close()
land=json.loads((out/'landscape-attribution.json').read_text());assert len(land['rows'])==45
noise=json.loads((out/'detail-persistence.json').read_text());assert len(noise['noise'])==40
assert len(json.loads((out/'coverage-scale.json').read_text()))==9
scripts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).resolve().parent.glob('*.py')}
(out/'verification.json').write_text(json.dumps({'production_similar_equivalence_pairs':count,'landscape_transform_rows':45,'noise_replicate_rows':40,'coverage_control_sources':9,'script_sha256':scripts},indent=2));print('Verified: 84 production similarity decisions; 45 landscape transform rows; 40 noise rows; 9 coverage controls.')
