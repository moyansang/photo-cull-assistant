"""Experimental burst grouping independent of photo-quality assessment.

Only prepared pixels, capture time and geometric subject regions are read.
No quality score, rejection, AI verdict or confirmation state is read.
Only decoder-failure notes affect input validity.
Camera and cohort references stay fixed; pose differences are not pair vetoes.
"""
from dataclasses import dataclass,replace
from pathlib import Path
import math
from itertools import permutations
from datetime import datetime,timezone
import cv2
import numpy as np
from PIL import Image, ImageOps

VERSION = 'scene-burst-camera-cohort-v10-presets'

@dataclass(frozen=True)
class BurstConfig:
    gap_seconds: float = 12.
    span_seconds: float = 90.
    scale_tolerance: float = .085
    translation: float = .075
    rotation: float = 5.
    min_inliers: int = 10
    min_inlier_ratio: float = .45
    appearance_overlap: float = .64
    strong_appearance: float = .80
    unknown_camera_scale_ratio: float = 1.7
    background_layout_error: float = .20
    background_framing_ratio: float = 1.25

CONFIG = BurstConfig()
PRESET_CONFIGS = {
    'strict': replace(CONFIG, gap_seconds=8., span_seconds=60.),
    'standard': CONFIG,
    'loose': replace(CONFIG, gap_seconds=18., span_seconds=120.),
}

def _capture_seconds(value):
    if value.tzinfo is None:return (value-datetime(1970,1,1)).total_seconds()
    return (value.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)).total_seconds()

@dataclass(frozen=True)
class BurstContext:
    time: float
    aspect: float
    points: np.ndarray
    descriptors: object
    shape: tuple
    grey: np.ndarray
    mask: np.ndarray
    cohort: np.ndarray
    people: tuple
    frame_hash: int
    background_texture: float = 0.

@dataclass(frozen=True)
class PersonAppearance:
    colour: np.ndarray
    vector: object = None
    pattern: object = None
    box: object = None

def _framing_evidence(left,right,cfg=CONFIG):
    """Large coherent framing changes from confidently matched face regions.

    This only resolves an unknown camera view. Missing detections, profile
    aspect changes or inconsistent scales stay unknown. Cached quality boxes
    cannot supply this evidence; boxes come from independent pixel detection.
    """
    pairs=[]
    for i,a in enumerate(left):
        for j,b in enumerate(right):
            if a.vector is None or b.vector is None or a.box is None or b.box is None:continue
            similarity=float(np.dot(a.vector,b.vector))
            if similarity<.45:continue
            _,_,aw,ah=a.box;_,_,bw,bh=b.box
            aspect=(bw/bh)/(aw/ah)
            if not 1/1.3<=aspect<=1.3:continue
            pairs.append((similarity,i,j,math.sqrt(bw*bh/(aw*ah))))
    used_left=set();used_right=set();scales=[]
    for _,i,j,scale in sorted(pairs,reverse=True):
        if i not in used_left and j not in used_right:
            used_left.add(i);used_right.add(j);scales.append(scale)
    if not scales or max(scales)/min(scales)>1.35:return False,{'state':'unknown','scales':scales}
    changed=(min(scales)>cfg.unknown_camera_scale_ratio or max(scales)<1/cfg.unknown_camera_scale_ratio)
    return changed,{'state':'different_framing' if changed else 'compatible','scales':scales}

def _cohort_match(left,right):
    """Injective coverage, with uncertain face observations kept unknown.

    A face nonmatch alone is not a conflict: independent apparel evidence must
    disagree as well. A shared matching companion cannot cover two people.
    """
    if not left or not right:return False,{'complete':False,'conflict':False,'unknown':True,'strong_identity':False}
    n,m=len(left),len(right)
    if max(n,m)>4:return False,{'complete':False,'conflict':False}
    scores=[];conflicts=[];identities=[]
    for a in left:
        row=[];bad=[];strong=[]
        for b in right:
            colour=_overlap(a.colour,b.colour)
            similarity=float(np.dot(a.vector,b.vector)) if a.vector is not None and b.vector is not None else None
            pattern=float(np.dot(a.pattern,b.pattern)) if a.pattern is not None and b.pattern is not None else None
            apparel_conflict=(colour<.45 and min(float(a.colour.max()),float(b.colour.max()))>.35)
            conflict=((similarity is not None and similarity<.15
                       and (colour<.72 or pattern is not None and pattern<.60))
                      or similarity is None and apparel_conflict)
            row.append(similarity is not None and similarity>=.30 or colour>=.70)
            bad.append(conflict)
            strong.append(similarity is not None and similarity>=.45)
        scores.append(row);conflicts.append(bad);identities.append(strong)
    # One visible face does not establish the complete cohort: another actor
    # may have turned away. Explicit replacement needs observed multi-person
    # evidence, rather than comparing whichever single face happened to detect.
    conflict=min(n,m)>1 and (any(all(row) for row in conflicts)
                            or any(all(conflicts[i][j] for i in range(n)) for j in range(m)))
    # A count change does not erase a supported replacement conflict. An extra
    # or missing uninformative observation remains unknown, not another person.
    def covers(matrix):
        if n<=m:return any(all(matrix[i][order[i]] and not conflicts[i][order[i]] for i in range(n))
                          for order in permutations(range(m),n))
        return any(all(matrix[order[j]][j] and not conflicts[order[j]][j] for j in range(m))
                   for order in permutations(range(n),m))
    matched=covers(scores);strong=covers(identities)
    unknown=any(a.vector is None or a.pattern is None for a in (*left,*right))
    return matched,{'complete':n==m,'conflict':conflict,'unknown':unknown,'strong_identity':strong,
                    'left_people':n,'right_people':m}

def _box(value):
    try:
        x,y,w,h=map(float,value)
        if all(math.isfinite(v) for v in (x,y,w,h)) and w>0 and h>0:
            x0,y0=max(0.,x),max(0.,y);x1,y1=min(1.,x+w),min(1.,y+h)
            if x1>x0 and y1>y0:return x0,y0,x1-x0,y1-y0
    except (TypeError,ValueError):pass
    return None

def _regions(asset):
    subject=getattr(asset,'subject_features',None)
    faces=[_box(r.get('box')) for r in getattr(subject,'analysis_faces',()) if isinstance(r,dict)]
    bodies=[_box(r.get('box')) for r in getattr(subject,'analysis_bodies',()) if isinstance(r,dict)]
    faces=[b for b in faces if b];bodies=[b for b in bodies if b]
    if not faces:
        face=_box(getattr(subject,'face',None))
        if face:faces=[face]
    return faces,bodies

def _paint(mask,box,value):
    h,w=mask.shape;x,y,bw,bh=box
    mask[max(0,int(y*h)):min(h,math.ceil((y+bh)*h)),max(0,int(x*w)):min(w,math.ceil((x+bw)*w))]=value

def _hist_bins(rgb):
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    # Hue/chroma plus a neutral category; no sharpness or luminance threshold.
    # All values fit in uint8 (0..33). Keep exact integer bins while avoiding
    # three full-frame int64 copies, and share one conversion across regions.
    hue=hsv[:,:,0]//12
    sat=hsv[:,:,1]
    return np.where(sat<35,30+hsv[:,:,2]//64,hue*2+(sat>=128))

def _hist(rgb,mask=None,bins=None):
    if bins is None:bins=_hist_bins(rgb)
    selected=bins[mask>0] if mask is not None else bins.ravel()
    hist=np.bincount(selected,minlength=34).astype(np.float32)
    return hist/max(1.,float(hist.sum()))

def _overlap(a,b):return float(np.minimum(a,b).sum())

def _pattern(rgb,box):
    x,y,w,h=box;ih,iw=rgb.shape[:2]
    crop=rgb[int(y*ih):max(int(y*ih)+1,int((y+h)*ih)),int(x*iw):max(int(x*iw)+1,int((x+w)*iw))]
    if min(crop.shape[:2])<8:return None
    gray=cv2.resize(cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY),(48,48)).astype(np.int16)
    # A lost texture observation is unknown; it can never assert another actor.
    if float(np.median(np.abs(np.diff(gray,axis=0))))<2:return None
    center=gray[1:-1,1:-1];bits=[]
    for dy,dx in ((-1,-1),(-1,0),(-1,1),(0,1),(1,1),(1,0),(1,-1),(0,-1)):
        bits.append(gray[1+dy:47+dy,1+dx:47+dx]>=center)
    count=sum(bit.astype(np.uint8) for bit in bits)
    changes=sum((bits[i]!=bits[(i+1)%8]).astype(np.uint8) for i in range(8))
    values=np.where(changes<=2,count,9)
    hist=np.bincount(values.ravel(),minlength=10).astype(np.float32)
    return hist/max(1.,float(np.linalg.norm(hist)))

def build_context(asset,identity=None):
    # Decoder failure is input validity, not a blur/quality recommendation.
    if any(str(n).startswith('decode_error:') for n in getattr(asset,'notes',())):return None
    path=getattr(asset,'preview_path',None)
    if not path:return None
    path=Path(path)
    # Reserved decoder placeholders are not photo evidence. Quality notes are
    # deliberately ignored even when valid prepared pixels are available.
    if path.parent.name=='unreadable':return None
    try:
        with Image.open(path) as source:
            image=ImageOps.exif_transpose(source).convert('RGB');image.thumbnail((640,640),Image.Resampling.LANCZOS)
            rgb=np.asarray(image).copy()
        time=_capture_seconds(asset.captured_at)
    except (OSError,ValueError,AttributeError):return None
    h,w=rgb.shape[:2];faces,bodies=_regions(asset)
    detected=()
    if identity is not None:
        from .face_identity import IdentityUnavailable
        try:detected=identity.faces(rgb)
        except (IdentityUnavailable,cv2.error):pass
        if detected:faces=[face.box for face in detected]
    foreground=np.zeros((h,w),np.uint8)
    for box in bodies:_paint(foreground,box,255)
    for x,y,bw,bh in faces:
        box=_box((x-1.3*bw,y-.4*bh,3.6*bw,7.2*bh))
        if box:_paint(foreground,box,255)
    if w>h:
        # Multi-person scenes often contain a back-facing person with no face
        # detection. Include the whole central foreground, not one visible face.
        _paint(foreground,(.08,.24,.84,.73),255)
    if not foreground.any():_paint(foreground,(.12,.12,.76,.84),255)
    background=np.full((h,w),255,np.uint8)
    for box in bodies:_paint(background,box,0)
    for x,y,bw,bh in faces:
        box=_box((x-1.7*bw,y-1.2*bh,4.4*bw,9*bh))
        if box:_paint(background,box,0)
    if w>h:_paint(background,(.12,.27,.70,.70),0)
    grey=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    orb=cv2.ORB_create(nfeatures=900,fastThreshold=9,edgeThreshold=15)
    points,descriptors=orb.detectAndCompute(grey,background)
    xy=np.array([k.pt for k in points],np.float32).reshape(-1,2)
    bins=_hist_bins(rgb)
    people=[]
    for x,y,bw,bh in faces:
        region=np.zeros((h,w),np.uint8)
        box=_box((x-.6*bw,y-.45*bh,2.2*bw,4.4*bh))
        if box:
            _paint(region,box,255)
            vector=next((f.vector for f in detected if f.box==(x,y,bw,bh)),None)
            garment=_box((x-.7*bw,y+1.1*bh,2.4*bw,3.2*bh))
            people.append(PersonAppearance(_hist(rgb,region,bins),vector,_pattern(rgb,garment) if garment else None,
                                           (x,y,bw,bh) if vector is not None else None))
    small=cv2.resize(grey,(9,8),interpolation=cv2.INTER_AREA)
    value=0
    for bit in (small[:,:-1]>small[:,1:]).flat:value=(value<<1)|int(bit)
    gradient=cv2.magnitude(cv2.Sobel(grey,cv2.CV_32F,1,0),cv2.Sobel(grey,cv2.CV_32F,0,1))
    texture=float(np.median(gradient[background>0])) if background.any() else 0.
    return BurstContext(time,w/h,xy,descriptors,(w,h),cv2.resize(grey,(96,96)),
        cv2.resize(background,(96,96),interpolation=cv2.INTER_NEAREST),_hist(rgb,foreground,bins),tuple(people),value,texture)

def camera_evidence(left,right,cfg=CONFIG):
    """Return same / different / unknown, with an anchored camera transform."""
    if max(left.aspect,right.aspect)/min(left.aspect,right.aspect)>1.08:return 'different',{}
    if left.descriptors is None or right.descriptors is None:return 'unknown',{}
    pairs=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(left.descriptors,right.descriptors,k=2)
    backward=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(right.descriptors,left.descriptors,k=2)
    reverse={a.queryIdx:a.trainIdx for pair in backward if len(pair)==2 for a,b in [pair] if a.distance<.76*b.distance}
    good=[a for pair in pairs if len(pair)==2 for a,b in [pair]
          if a.distance<.76*b.distance and reverse.get(a.trainIdx)==a.queryIdx]
    if len(good)<cfg.min_inliers:
        state=('different' if len(good)<3 and min(len(left.points),len(right.points))>80
               and min(left.background_texture,right.background_texture)>60 else 'unknown')
        return state,{'matches':len(good)}
    a=np.array([left.points[m.queryIdx] for m in good]);b=np.array([right.points[m.trainIdx] for m in good])
    matrix,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.5,maxIters=2000,confidence=.99)
    if matrix is None:return 'unknown',{'matches':len(good)}
    valid=inliers.ravel().astype(bool);n=int(valid.sum());ratio=n/len(good)
    if n<cfg.min_inliers or ratio<cfg.min_inlier_ratio:return 'unknown',{'inliers':n,'ratio':ratio}
    # Convert the affine map to normalized image coordinates.
    linear=matrix[:,:2]@np.diag(left.shape)/np.array(right.shape)[:,None]
    scale=math.sqrt(abs(float(np.linalg.det(linear))))
    # Rotation uses square pixels. Normalized coordinates stretch a landscape
    # frame vertically, so their angle is not the physical camera rotation.
    rotation=abs(math.degrees(math.atan2(matrix[1,0],matrix[0,0])))
    origin_shift=matrix[:,2]/np.array(right.shape)
    # Rotation is around the camera's centre, not the image's upper-left corner.
    # Its origin intercept is not a physical pan (e.g. a 4-degree handheld tilt).
    shift=linear@np.array([.5,.5])+origin_shift-np.array([.5,.5])
    points=a[valid]/np.array(left.shape)
    span=np.ptp(points,axis=0)
    area=float(cv2.contourArea(cv2.convexHull(points.astype(np.float32)))) if n>=3 else 0.
    info=dict(inliers=n,ratio=ratio,scale=scale,rotation=rotation,translation=shift.tolist(),span=span.tolist(),area=area)
    target_points=b[valid]/np.array(right.shape)
    target_span=np.ptp(target_points,axis=0)
    if max(span)<.18 or area<.008 or max(target_span)<.18 or not .5<scale<2.:
        return 'unknown',info
    if abs(scale-1)>cfg.scale_tolerance or rotation>cfg.rotation or max(abs(shift))>cfg.translation:
        return 'different',info
    return 'same',info

def pair_evidence(anchor,candidate,cfg=CONFIG):
    camera,geometry=camera_evidence(anchor,candidate,cfg)
    framing_changed,framing=_framing_evidence(anchor.people,candidate.people,cfg)
    # Only common background pixels count. Remove a global exposure offset so
    # lighting changes and flat backdrops do not invent a camera difference.
    mask=(anchor.mask>0)&(candidate.mask>0)
    locations=np.argwhere(mask)
    sufficient=bool(float(mask.mean())>=.10 and len(locations)>0
                    and min(np.ptp(locations,axis=0))>=18)
    differences=(anchor.grey.astype(float)-candidate.grey.astype(float))[mask]
    layout_error=float(np.mean(np.abs(differences-np.median(differences)))/255) if sufficient else None
    layout=dict(common_fraction=float(mask.mean()),error=layout_error,sufficient=sufficient)
    # Dynamic stage lighting can rearrange the background luminance while a
    # matched actor remains at the same image scale. Treat that as inconclusive;
    # a large context change needs missing or changed framing support as well.
    scales=framing['scales']
    stable_framing=(framing['state']=='compatible' and scales
                    and min(scales)>=1/cfg.background_framing_ratio and max(scales)<=cfg.background_framing_ratio)
    background_changed=sufficient and layout_error>cfg.background_layout_error and not stable_framing
    appearance=_overlap(anchor.cohort,candidate.cohort)
    # Mutual cohort coverage: a single shared companion is insufficient when
    # other visible people have independent, incompatible apparel evidence.
    people,coverage=_cohort_match(anchor.people,candidate.people)
    if camera=='different' or camera=='unknown' and (framing_changed or background_changed):accepted=False
    elif coverage['conflict']:accepted=False
    elif camera=='same':
        accepted=(people and (appearance>=cfg.appearance_overlap or coverage.get('strong_identity',False))
                  or coverage.get('unknown',False) and appearance>=cfg.strong_appearance)
    else:
        # Missing identity/texture is unknown, never a pose or blur veto. Known
        # weak person correspondence cannot be rescued by global colours alone.
        # Independent camera or cohort conflicts above still reject the pair.
        partial_reference=(coverage.get('left_people',0)>coverage.get('right_people',0))
        # When camera geometry is also unknown, matching just a subset of a
        # known cohort needs strong identity and whole-cohort corroboration.
        # A supported camera or an actually missing observation remains usable;
        # face count alone never asserts that another person has appeared.
        accepted=(people and appearance>=(cfg.strong_appearance if partial_reference else .70)
                  and (not partial_reference or coverage.get('strong_identity',False) or coverage.get('unknown',False))
                  or coverage.get('unknown',False) and appearance>=cfg.strong_appearance)
    return bool(accepted),dict(camera=camera,geometry=geometry,framing=framing,background_layout=layout,
                              cohort_overlap=appearance,people_coverage=coverage)

def assign_bursts(assets,protected,source_id,check_cancel,stats,cfg=CONFIG,*,preserve_existing=False,context_cache=None):
    from .face_identity import IdentityEngine,IdentityUnavailable
    try:identity=IdentityEngine() if any(_regions(a)[0] for a in assets) else None
    except IdentityUnavailable:identity=None
    def context(asset):
        if context_cache is None:return build_context(asset,identity)
        key=id(asset)
        if key not in context_cache:context_cache[key]=build_context(asset,identity)
        return context_cache[key]
    ordered=sorted(assets,key=lambda a:(_capture_seconds(a.captured_at),a.stem.casefold(),source_id(a)))
    pending=[];existing={};groups=[];staged=[]
    for asset in ordered:
        if asset.group_id>0 and (preserve_existing or asset.group_id in protected):
            if asset.group_id not in protected:existing.setdefault(asset.group_id,[]).append(asset)
        else:pending.append(asset)
    for gid,members in sorted(existing.items()):
        check_cancel()
        saved={a.grouping_anchor_id for a in members if a.grouping_anchor_id}
        reference=members[0]
        if saved:
            reference=next((a for a in members if len(saved)==1 and source_id(a) in saved),None)
        anchor=context(reference) if reference is not None else None
        person_reference=anchor
        locked=bool(anchor is not None and len(anchor.people)>1)
        # Reconstruct the same first corroborated cohort after reopening. The
        # saved camera anchor never moves, and no feature vectors are persisted.
        # Otherwise a one-face anchor would forget its later supported cohort
        # and a new photo could bridge through the one shared companion.
        if pending and anchor is not None and not locked:
            for member in members:
                if member is reference:continue
                check_cancel();observed=context(member)
                if observed is None or len(observed.people)<=len(person_reference.people):continue
                accepted,info=pair_evidence(replace(anchor,people=person_reference.people),observed,cfg)
                if (accepted and (info['camera']=='same' or info['people_coverage'].get('strong_identity',False))
                        and info['cohort_overlap']>=cfg.strong_appearance):
                    person_reference=observed;locked=True;break
        groups.append([anchor,max(_capture_seconds(a.captured_at) for a in members),[],gid,
                       next(iter(saved)) if len(saved)==1 else source_id(members[0]),person_reference,locked])
    next_id=max((0,*protected,*(existing if preserve_existing else ())))+1
    stats.pose_candidates=stats.pose_matches=stats.pose_component_rejections=0
    for asset in pending:
        check_cancel();observation=context(asset)
        found=None;best_rank=None;found_info=None
        if observation is not None:
            for group in reversed(groups):
                anchor,last,members,_,_,reference,_=group
                if anchor is None:continue
                gap=max(0.,anchor.time-observation.time,observation.time-last)
                span=max(last,observation.time)-min(anchor.time,observation.time)
                if gap>cfg.gap_seconds or span>cfg.span_seconds:continue
                check_cancel();stats.pose_candidates+=1
                accepted,info=pair_evidence(replace(anchor,people=reference.people),observation,cfg)
                if accepted:
                    # Prefer positive camera correspondence over a merely
                    # unknown view; a recent fragment cannot steal a known burst.
                    rank=(info['camera']=='same',info['people_coverage'].get('strong_identity',False),
                          info['geometry'].get('ratio',0.) if info['camera']=='same' else 0.,-group[3])
                    if best_rank is None or rank>best_rank:found=group;best_rank=rank;found_info=info
                else:stats.pose_component_rejections+=1
        if found is None:
            found=[observation,observation.time if observation else 0.,[],next_id,source_id(asset),observation,
                   bool(observation is not None and len(observation.people)>1)]
            groups.append(found);next_id+=1
        else:
            stats.pose_matches+=1;found[1]=max(found[1],observation.time)
            # Fix the first corroborated multi-person reference. This is not a
            # rolling union of every face: later new principals cannot enroll.
            if (not found[6] and len(observation.people)>len(found[5].people)
                    and (found_info['camera']=='same' or found_info['people_coverage'].get('strong_identity',False))
                    and found_info['cohort_overlap']>=cfg.strong_appearance):
                found[5]=observation;found[6]=True
        found[2].append(asset)
    for _,_,members,gid,anchor,_,_ in groups:
        for asset in members:staged.append((asset,gid,anchor))
    check_cancel()
    for asset,gid,anchor in staged:asset.group_id=gid;asset.grouping_anchor_id=anchor
    if identity is not None:identity.clear()
    stats.photos=len(assets);stats.representatives=len(groups)
    return assets


def _time_compatible(left,right,cfg):
    a=[_capture_seconds(x.captured_at) for x in left]
    b=[_capture_seconds(x.captured_at) for x in right]
    gap=max(0.,min(a)-max(b),min(b)-max(a))
    span=max(*a,*b)-min(*a,*b)
    return gap<=cfg.gap_seconds and span<=cfg.span_seconds


def assign_preset_bursts(assets,protected,source_id,check_cancel,stats,cfg=CONFIG,*,preserve_existing=False):
    """Refine fixed standard groups without letting time windows steal members.

    Strict only splits standard groups. Loose treats each standard group as an
    atomic unit and requires every cross-member pair in both directions. Saved
    groups keep their IDs/anchors and cannot merge with another saved group.
    Contexts for loose checks exist only during this call, never on disk.
    """
    if cfg is CONFIG:
        return assign_bursts(assets,protected,source_id,check_cancel,stats,cfg,preserve_existing=preserve_existing)
    staged=[replace(a) for a in assets]
    saved={id(b) for a,b in zip(assets,staged) if preserve_existing and a.group_id>0}
    cache={} if cfg.gap_seconds>CONFIG.gap_seconds else None
    identity=None;identity_checked=False
    try:
        assign_bursts(staged,protected,source_id,check_cancel,stats,CONFIG,
                      preserve_existing=preserve_existing,context_cache=cache)
        canonical={}
        for a in staged:
            if a.group_id not in protected:canonical.setdefault(a.group_id,[]).append(a)
        next_id=max((0,*protected,*(a.group_id for a in staged)))+1
        def ordered(items):return sorted(items,key=lambda a:(_capture_seconds(a.captured_at),a.stem.casefold(),source_id(a)))
        if cache is None:
            count=0
            for gid,members in sorted(canonical.items()):
                check_cancel();old=[a for a in members if id(a) in saved]
                pieces=[old] if old else []
                for asset in ordered([a for a in members if id(a) not in saved]):
                    check_cancel()
                    target=next((piece for piece in pieces if _time_compatible(piece,[asset],cfg)),None)
                    if target is None:pieces.append([asset])
                    else:target.append(asset)
                for i,piece in enumerate(pieces):
                    piece_id=gid if i==0 else next_id
                    if i>0:next_id+=1
                    anchor=(next((a.grouping_anchor_id for a in old if a.grouping_anchor_id),source_id(ordered(old)[0]))
                            if i==0 and old else source_id(ordered(piece)[0]))
                    for asset in piece:
                        if id(asset) not in saved:asset.group_id=piece_id;asset.grouping_anchor_id=anchor
                count+=len(pieces)
        else:
            merged=[]
            def context(asset):
                nonlocal identity,identity_checked
                key=id(asset)
                if key not in cache:
                    if not identity_checked:
                        from .face_identity import IdentityEngine,IdentityUnavailable
                        try:identity=IdentityEngine() if any(_regions(a)[0] for a in staged) else None
                        except IdentityUnavailable:identity=None
                        identity_checked=True
                    cache[key]=build_context(asset,identity)
                return cache[key]
            def compatible(left,right):
                for a in left:
                    for b in right:
                        check_cancel();ca=context(a);cb=context(b)
                        if ca is None or cb is None:return False
                        stats.pose_candidates+=1
                        if not pair_evidence(ca,cb,cfg)[0]:stats.pose_component_rejections+=1;return False
                        stats.pose_candidates+=1
                        if not pair_evidence(cb,ca,cfg)[0]:stats.pose_component_rejections+=1;return False
                return True
            rows=sorted(canonical.items(),key=lambda item:(not any(id(a) in saved for a in item[1]),
                min(_capture_seconds(a.captured_at) for a in item[1]),item[0]))
            for gid,members in rows:
                check_cancel();has_saved=any(id(a) in saved for a in members)
                anchors={a.grouping_anchor_id for a in members if id(a) in saved and a.grouping_anchor_id}
                sealed=bool(anchors and (len(anchors)!=1 or not any(source_id(a) in anchors for a in members)))
                anchor=(next(iter(anchors)) if len(anchors)==1 else source_id(ordered(members)[0]))
                target=None
                for other in merged:
                    if sealed or other['sealed']:continue
                    if has_saved and other['saved']:continue
                    # An overlapping standard-time split is a scene/reference
                    # decision, not a pause that the loose preset may bridge.
                    if _time_compatible(other['members'],members,CONFIG):continue
                    if _time_compatible(other['members'],members,cfg) and compatible(other['members'],members):
                        target=other;break
                if target is None:
                    merged.append(dict(members=list(members),gid=gid,anchor=anchor,saved=has_saved,sealed=sealed))
                else:
                    for asset in members:
                        asset.group_id=target['gid'];asset.grouping_anchor_id=target['anchor']
                    target['members'].extend(members)
                    stats.pose_matches+=len(members)
            count=len(merged);stats.portrait_contexts=len(cache)
        # Keep chronological IDs for fresh groups while reserving every saved
        # and protected ID. Existing memberships are never renumbered.
        fixed=set(protected)|{a.group_id for a in assets if preserve_existing and a.group_id>0}
        fresh={}
        for asset in staged:
            if asset.group_id not in fixed:fresh.setdefault(asset.group_id,[]).append(asset)
        next_id=max((0,*fixed))+1
        for members in sorted(fresh.values(),key=lambda items:(_capture_seconds(ordered(items)[0].captured_at),
                                                                source_id(ordered(items)[0]))):
            for asset in members:asset.group_id=next_id
            next_id+=1
        check_cancel()
        for original,result in zip(assets,staged):
            original.group_id=result.group_id;original.grouping_anchor_id=result.grouping_anchor_id
        stats.representatives=count
        return assets
    finally:
        if cache is not None:cache.clear()
        if identity is not None:identity.clear()
