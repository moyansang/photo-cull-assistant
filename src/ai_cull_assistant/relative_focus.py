"""Comparable burst frames may raise a focus concern, never prove sharpness."""
from collections import defaultdict
from statistics import median
import math


def _shape(asset):
    subject=asset.subject_features
    face=getattr(subject,'face',None)
    points=getattr(subject,'landmarks',None)
    if not face or not points or len(points)!=5:return None
    dx,dy=points[1][0]-points[0][0],points[1][1]-points[0][1]
    return face, math.atan2(dy,dx)%math.pi


def _comparable(a,b):
    sa,sb=_shape(a),_shape(b)
    if sa is None or sb is None:return False
    fa,aa=sa;fb,ab=sb
    delta=abs(aa-ab);delta=min(delta,math.pi-delta)
    return (.8 <= fa[2]/max(fb[2],1e-8) <= 1.25
            and math.hypot(fa[0]+fa[2]/2-fb[0]-fb[2]/2,fa[1]+fa[3]/2-fb[1]-fb[3]/2)<.12
            and delta<math.radians(12))


def flag_relative_focus(assets, candidates):
    """Modify only candidates; clear reference frames remain unchanged."""
    groups=defaultdict(list)
    for asset in assets:
        if asset.clarity_version=='clarity-v2' and not asset.auto_rejected:
            groups[asset.group_id].append(asset)
    changes=[]
    for asset in candidates:
        evidence=asset.clarity_evidence or {}
        if asset.clarity_version!='clarity-v2' or evidence.get('state')!='clear':continue
        fine=evidence.get('fine',{})
        peers=[other.clarity_evidence for other in groups[asset.group_id]
               if other is not asset and _comparable(asset,other)
               and (other.clarity_evidence or {}).get('state')=='clear']
        if len(peers)<2:continue
        details=sorted((p.get('fine',{}).get('laplacian_normalized',0) for p in peers),reverse=True)[:2]
        curves=[p.get('fine',{}).get('edge_curvature',0) for p in peers]
        if (fine.get('laplacian_normalized',0)<.45*median(details)
                and fine.get('edge_curvature',0)<.80*median(curves)):
            asset.clarity_evidence={**evidence,'state':'uncertain',
                'reasons':list(evidence.get('reasons',[]))+['weaker_than_comparable_burst'],
                'relative_reference_count':len(peers)}
            asset.screening_reason='face_focus_uncertain'
            changes.append(asset)
    return changes
