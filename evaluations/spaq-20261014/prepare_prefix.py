"""Read a LOCAL bounded SPAQ tgz prefix; no network, no tar path extraction.
Needs openpyxl for read-only annotation parsing. Stops at prefix truncation.
"""
import argparse,hashlib,json,re,tarfile
from pathlib import Path
import openpyxl
p=argparse.ArgumentParser();p.add_argument('--prefix',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
a.output.mkdir(exist_ok=True,parents=True);images=a.output/'images';images.mkdir(exist_ok=True)
allowed={'Scene category labels.xlsx','MOS and Image attribute scores.xlsx','EXIF_tags.xlsx'}
meta=None;selected=[];seen=[]
def rows(path):
 it=iter(openpyxl.load_workbook(path,read_only=True,data_only=True).active.values);head=next(it);return [dict(zip(head,r)) for r in it]
try:
 with tarfile.open(a.prefix,'r|gz') as t:
  for member in t:
   name=Path(member.name).name
   if member.isfile() and member.name.startswith('SPAQ/Annotations/') and name in allowed:
    assert member.size<2_000_000
    (a.output/name).write_bytes(t.extractfile(member).read())
   elif member.isfile() and member.name.startswith('SPAQ/TestImage/') and re.fullmatch(r'\d{5}\.jpg',name):
    if meta is None:
     quality={r['Image name']:r for r in rows(a.output/'MOS and Image attribute scores.xlsx')}
     joined=[dict(r,**quality[r['Image name']]) for r in rows(a.output/'Scene category labels.xlsx')]
     meta={r['Image name']:r for r in joined};(a.output/'annotations.json').write_text(json.dumps(joined,indent=2))
    entry={'name':name,'bytes':member.size,'landscape':meta[name]['Landscape']};seen.append(entry)
    if entry['landscape']>0 and len(selected)<60:
     assert member.size<25_000_000
     content=t.extractfile(member).read();assert len(content)==member.size
     (images/name).write_bytes(content);entry['sha256']=hashlib.sha256(content).hexdigest();selected.append(entry)
except (tarfile.ReadError,EOFError) as e:
 print('Expected truncated-prefix boundary:',str(e))
(a.output/'prefix-extraction.json').write_text(json.dumps({'image_headers_seen':len(seen),'selected':selected,'seen':seen},indent=2));print('selected',len(selected))
