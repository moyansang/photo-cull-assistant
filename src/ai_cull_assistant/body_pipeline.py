"""Opt-in body evidence, with separate cache and conservative aggregation."""
from pathlib import Path
import hashlib
import json

# This versions only disposable local evidence, not the body scoring policy.
LOCAL_CACHE_VERSION = "source-stat-v2"


def apply_body_check(asset, screened, settings, cache_dir):
    # An explicit body list (including []) owns body scope. Never resurrect
    # a face-associated automatic body or its cache after a manual override.
    if 'selected_bodies' in settings.photos.get(settings.key(asset), {}):
        return screened
    if screened.rejected or not screened.face_found or not screened.focus_evidence:
        return screened
    from .subject import detail_features, detail_features_list
    from .shared_decode import full_image
    from .measurement_receipts import stage, cache_hit
    from .body_focus import assess_body_focus, VERSION
    from .ai_project import atomic_json
    explicit = 'selected_faces' in settings.photos.get(settings.key(asset), {})
    subjects = detail_features_list(asset, settings) if explicit else [detail_features(asset, settings)]
    subjects = [subject for subject in subjects if subject is not None and subject.face is not None]
    if not subjects:
        return screened
    source=asset.jpg_path or asset.raw_path or asset.primary_path
    from .image_decode import source_signature, asset_source_signature
    primary_signature=source_signature(source)
    source_signatures=asset_source_signature(asset)
    bodies=[]
    for index, subject in enumerate(subjects, 1):
        identity=dict(source=primary_signature, sources=source_signatures,
                      cache_identity=LOCAL_CACHE_VERSION, face=subject.face,version=VERSION)
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        target=Path(cache_dir)/VERSION/(digest+'.json') if cache_dir else None
        body=None
        if target and target.is_file():
            try:
                body=json.loads(target.read_text('utf-8'))
                if asset_source_signature(asset)!=source_signatures:body=None
            except (OSError,ValueError):pass
        if not isinstance(body,dict) or body.get('version')!=VERSION or body.get('state') not in {'clear','uncertain','severe_blur'}:
            with full_image(asset) as image:
                with stage(asset, "body_focus", image, options={}, regions=[tuple(subject.face)]):
                    body=assess_body_focus(image,tuple(subject.face))
            if (target and 'body_models_unavailable' not in body.get('reasons',[])
                    and asset_source_signature(asset)==source_signatures):
                atomic_json(target,body)
        else:
            cache_hit(asset, "body_focus")
        bodies.append(dict(body,participant=index))
    if len(bodies)==1:
        body={key:value for key,value in bodies[0].items() if key!='participant'}
    else:
        severe=any(item['state']=='severe_blur' and item.get('review_kind')=='motion_confirmed' for item in bodies)
        clear=all(item['state']=='clear' for item in bodies)
        body=dict(version=VERSION,state='severe_blur' if severe else 'clear' if clear else 'uncertain',
                  review_kind='motion_confirmed' if severe else 'clear' if clear else 'unsupported',
                  reasons=list(dict.fromkeys(reason for item in bodies for reason in item.get('reasons',[]))),
                  participants=bodies,regions=[])
    face=dict(screened.focus_evidence or {'state':'uncertain','reasons':['missing_face_evidence']})
    evidence={**face,'face_state':face['state'],'body':body}
    if body['state']=='severe_blur' and body.get('review_kind')=='motion_confirmed':
        evidence['state']='severe_blur';screened.rejected=True;screened.reason='obvious_body_blur'
    elif body['state']!='clear':
        evidence['state']='uncertain';screened.reason='body_focus_uncertain'
    evidence['face_reasons']=list(face.get('face_reasons', face.get('reasons', [])))
    evidence['body_reasons']=list(body.get('reasons', []))
    evidence['reason_schema_version']='part-reasons-v1'
    evidence['reasons']=evidence['face_reasons']+evidence['body_reasons']
    screened.focus_evidence=evidence
    return screened
