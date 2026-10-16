"""Pure display/edit vs analysis ROI contract. Does not write assets or regroup.
Normalized xywh; legacy empty-face semantics are explicit and preserved by default.
"""
from __future__ import annotations
import copy, hashlib, json, math

SCHEMA_VERSION='roi-contract-v1'


def _normalize(items,kind,origin):
    if not isinstance(items,list):raise ValueError(f'{kind} must be a list; absence and [] differ')
    out=[]
    for item in items:
        record=copy.deepcopy(item) if isinstance(item,dict) else {'box':copy.deepcopy(item)}
        box=record.get('box')
        if not isinstance(box,(list,tuple)) or len(box)!=4:raise ValueError('box must be normalized xywh')
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in box):raise ValueError('box must be finite numeric xywh')
        x,y,w,h=[0.0 if float(v)==0 else float(v) for v in box]
        if x<0 or y<0 or w<=0 or h<=0 or x+w>1+1e-9 or y+h>1+1e-9:raise ValueError('box outside image')
        # Only identity/box/origin/kind affect the ROI contract, not computed metrics.
        entry={'box':[x,y,w,h],'kind':kind,'origin':origin}
        if 'id' in record:entry['id']=str(record['id'])
        out.append(entry)
    return out


def roi_list_digest(regions):
    return hashlib.sha256(json.dumps(regions,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def resolve_rois(auto_faces,auto_bodies,overrides=None,*,empty_face_policy=None):
    """legacy_all: existing explicit empty selected_faces suppresses auto bodies.
    per_kind: new-version records can opt in; [] disables only that category.
    Selected bodies always remain authoritative, including explicit [].
    hidden/subjects_disabled hides and disables all. Face display never gates analysis.
    """
    o=copy.deepcopy({} if overrides is None else overrides)
    if not isinstance(o,dict):raise ValueError('overrides must be an object')
    version=o.get('roi_selection_schema',1)
    if isinstance(version,bool) or not isinstance(version,int) or version not in (1,2):raise ValueError('unsupported ROI selection schema')
    empty_face_policy=empty_face_policy or ('per_kind' if version==2 else 'legacy_all')
    if empty_face_policy not in ('legacy_all','per_kind'):raise ValueError('invalid empty-face policy')
    if empty_face_policy=='per_kind' and version!=2:raise ValueError('per-kind semantics require roi_selection_schema=2')
    af=_normalize(auto_faces,'face','automatic');ab=_normalize(auto_bodies,'body','automatic')
    faces=_normalize(o['selected_faces'],'face','manual') if 'selected_faces' in o else af
    bodies=_normalize(o['selected_bodies'],'body','manual') if 'selected_bodies' in o else ab
    disabled=[]
    if 'selected_faces' in o and not faces:
        disabled.append('faces')
        if empty_face_policy=='legacy_all' and 'selected_bodies' not in o:bodies=[];disabled.append('automatic_bodies_legacy')
    if 'selected_bodies' in o and not bodies:disabled.append('bodies')
    if o.get('hidden') or o.get('subjects_disabled'):
        faces=[];bodies=[];disabled=['all_subjects']
    display=faces if faces else bodies
    def primary(kind,regions):
        key='primary_'+kind+'_index'
        if not regions:return None
        identity_key='primary_'+kind+'_id'
        if identity_key in o:
            found=[i for i,r in enumerate(regions) if r.get('id')==str(o[identity_key])]
            if len(found)!=1:raise ValueError('stale or ambiguous '+identity_key)
            return found[0]
        if key in o:
            if any(r['origin']=='automatic' for r in regions):
                if o.get('primary_'+kind+'_basis')!=roi_list_digest(regions):raise ValueError('stale or unbound '+key)
            idx=o[key]
            if isinstance(idx,bool) or not isinstance(idx,int) or not 0<=idx<len(regions):raise ValueError('invalid '+key)
            return idx
        return 0 if len(regions)==1 else None
    pface,pbody=primary('face',faces),primary('body',bodies)
    signature={'schema':SCHEMA_VERSION,'faces':faces,'bodies':bodies,'disabled':disabled,'empty_face_policy':empty_face_policy,'primary_face_index':pface,'primary_body_index':pbody}
    digest=hashlib.sha256(json.dumps(signature,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'schema':SCHEMA_VERSION,'display_regions':copy.deepcopy(display),'edit_regions':copy.deepcopy(display),
            'analysis_faces':copy.deepcopy(faces),'analysis_bodies':copy.deepcopy(bodies),
            'disabled':disabled,'empty_face_policy':empty_face_policy,'revision_digest':digest,
            'primary_face_index':pface,'primary_body_index':pbody,
            'face_rois_digest':roi_list_digest(faces),'body_rois_digest':roi_list_digest(bodies),
            'primary_selection_required':(len(faces)>1 and pface is None) or (len(bodies)>1 and pbody is None)}


def roi_change(previous,current):
    """Per-asset invalidation hint. Batch-save/regroup orchestration is external."""
    changed=previous['revision_digest']!=current['revision_digest']
    return {'changed':changed,'previous_roi_digest':previous['revision_digest'],
            'new_roi_digest':current['revision_digest'],
            'invalidate_features':['face','body','grouping_visual','local_quality'] if changed else [],
            'regroup_requested':changed}
