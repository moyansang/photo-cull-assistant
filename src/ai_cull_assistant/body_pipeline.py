"""Opt-in body evidence, with separate cache and conservative aggregation."""
from pathlib import Path
import hashlib
import json


def apply_body_check(asset, screened, settings, cache_dir):
    if screened.rejected or not screened.face_found or not screened.focus_evidence:
        return screened
    from .subject import detail_features
    from .face_focus import load_full_image
    from .body_focus import assess_body_focus, VERSION
    from .ai_project import atomic_json
    subject=detail_features(asset,settings)
    if subject is None or subject.face is None:return screened
    source=asset.raw_path or asset.primary_path
    st=source.stat()
    identity=dict(path=str(source.resolve()),size=st.st_size,mtime=st.st_mtime_ns,
                  face=subject.face,version=VERSION)
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    target=Path(cache_dir)/VERSION/(digest+'.json') if cache_dir else None
    body=None
    if target and target.is_file():
        try:body=json.loads(target.read_text('utf-8'))
        except (OSError,ValueError):pass
    if not isinstance(body,dict) or body.get('version')!=VERSION or body.get('state') not in {'clear','uncertain','severe_blur'}:
        with load_full_image(asset) as image:body=assess_body_focus(image,tuple(subject.face))
        if target and 'body_models_unavailable' not in body.get('reasons',[]):atomic_json(target,body)
    face=dict(screened.focus_evidence or {'state':'uncertain','reasons':['missing_face_evidence']})
    evidence={**face,'face_state':face['state'],'body':body}
    if body['state']=='severe_blur' and body.get('review_kind')=='motion_confirmed':
        evidence['state']='severe_blur';screened.rejected=True;screened.reason='obvious_body_blur'
    elif body['state']!='clear':
        evidence['state']='uncertain';screened.reason='body_focus_uncertain'
    evidence['reasons']=list(face.get('reasons',[]))+list(body.get('reasons',[]))
    screened.focus_evidence=evidence
    return screened
