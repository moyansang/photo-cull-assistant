"""Download a bounded official OSF page subset; verify supplied file hashes."""
import concurrent.futures,hashlib,json,pathlib,urllib.request,argparse
p=argparse.ArgumentParser();p.add_argument('--listing',required=True);p.add_argument('--output',required=True);p.add_argument('--limit',type=int,default=60);a=p.parse_args()
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
items=json.load(open(a.listing))['data'][:a.limit]
def get(x):
 name=x['attributes']['name']; path=out/name; expected=x['attributes']['extra']['hashes']['sha256'];result={'name':name,'url':x['links']['download'],'expected_sha256':expected}
 try:
  if not path.exists():
   with urllib.request.urlopen(result['url'],timeout=75) as f: content=f.read(3_000_001)
   if len(content)>3_000_000:raise ValueError('bounded image size exceeded')
   if hashlib.sha256(content).hexdigest()!=expected:raise ValueError('checksum mismatch')
   path.write_bytes(content)
  assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
  result.update(status='ok',bytes=path.stat().st_size)
 except Exception as e:result.update(status='error',error=str(e))
 print(name,result['status'],flush=True);return result
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:results=list(ex.map(get,items))
(out.parent/'downloads.json').write_text(json.dumps(results,indent=2))
