"""Real local pipeline + real image payloads; only outbound HTTP is simulated."""
import base64
import copy
import io
import json
import re
from threading import Event

import numpy as np
from PIL import Image, ImageDraw

from ai_cull_assistant import ai_api, ai_focus
from ai_cull_assistant.ai_project import ReviewProject, photo_id
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.lightroom_results import current_focus, current_selection
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.workflow import persist_manual_groups
from quality_cases import confirmed_settings, protocol_reply

PROFILE={'id':'synthetic-acceptance','name':'Synthetic acceptance',
         'base_url':'https://example.invalid/v1','model':'test-model'}


def test_scan_payload_response_manual_export_crop_regroup_wired(tmp_path,monkeypatch):
    source=tmp_path/'photos';source.mkdir()
    workspace=tmp_path/'workspace'
    colors=((245,30,35),(20,210,55),(35,65,240),(245,220,25))
    image=Image.new('RGB',(640,480),'gray');draw=ImageDraw.Draw(image)
    # Unique large corner markers would disappear in a subject-only crop.
    for box,color in zip(((0,0,100,100),(539,0,639,100),(0,379,100,479),(539,379,639,479)),colors):
        draw.rectangle(box,fill=color)
    for x in range(210,430,12):draw.line((x,120,x,360),fill='white',width=3)
    for name in ('A','B'):image.save(source/f'{name}.png')
    Image.new('RGB',(480,640),'skyblue').save(source/'C.png')
    sidecar=source/'A.xmp';sidecar.write_bytes(b'Existing Lightroom rating and edits')
    original={p:p.read_bytes() for p in source.iterdir()}
    crops=CropSettings(photos={str((source/f'{name}.png').resolve()).casefold():
        {'manual_face':[.32,.25,.35,.5]} for name in ('A','B')})
    options={'technical_screening':True,'analysis_mode':'portrait','columns':3}
    requests=[]
    monkeypatch.setattr(ai_api,'get_secret',lambda _: 'synthetic-key-never-persist')
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def transport(request,timeout):
        payload=json.loads(request.data);requests.append(payload)
        prompt=payload['messages'][0]['content'][0]['text']
        if '"photo_identity"' in prompt:
            answer=json.loads(protocol_reply(prompt)['text'])
        else:
            task_id=re.search(r'"task_id":"([0-9a-f]+)"',prompt).group(1)
            batch_id=re.search(r'"batch_id":"(B\d+)"',prompt).group(1)
            ids=list(dict.fromkeys(re.findall(r'(?m)^(P[A-F0-9]{12}) \|',prompt)))
            answer={'task_id':task_id,'batch_id':batch_id,'photos':[
                {'photo_id':pid,'rating':4,'suggest_reject':False,
                 'reason':'Synthetic full-frame composition comparison','review_items':[]} for pid in ids]}
        return Response(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
    monkeypatch.setattr(ai_api,'_open_request',transport)

    # Actual scan, source decode, local analysis, grouping, sheets and checkpoint.
    result=start_job(source,workspace,options,crops,mode='scan').run(
        options,crops,Event(),None,focus_profile=PROFILE)
    assert requests==[], 'Configured API must not run during local scan'
    assert len(result.assets)==3 and all(a.clarity_evidence for a in result.assets)
    assets={a.stem:a for a in result.assets}
    assert assets['A'].group_id==assets['B'].group_id
    # Explicitly model the human save/confirm action before the automatic
    # local rescan. Merely having a legacy manual_face is insufficient.
    for name in ('A','B'):
        confirmed_settings(assets[name],crops)
        assets[name].ai_focus_dirty=True
    result=start_job(source,workspace,options,crops,result=result,mode='rescan').run(
        options,crops,Event(),None,focus_profile=PROFILE)
    assets={a.stem:a for a in result.assets}
    assert requests==[]
    persist_manual_groups(result)
    project=ReviewProject(workspace)

    def select():
        task=project.create_task(result.assets,crops,{},photo_ids=[photo_id(assets['A']),photo_id(assets['B'])])
        batch=task['batches'][0]
        prompt=project.prompt(task,batch)
        paths=project.batch_images(task,batch)
        reply=ai_api.call_model(PROFILE,prompt,paths)
        assert project.ingest(task,batch,reply['text'])==[]
        return task,batch,prompt,paths

    task,batch,prompt,paths=select()
    assert len(requests)==1 and '不实施清晰度质量判定' in prompt
    assert '完整画面' in prompt and '"local"' not in prompt
    assert 'ai_technical' not in prompt
    content=requests[0]['messages'][0]['content']
    assert len(content)==1+len(paths), 'Selection sends full-frame sheets, no forced technical package'
    for item,path in zip(content[1:],paths):
        raw=base64.b64decode(item['image_url']['url'].split(',',1)[1])
        assert raw==path.read_bytes()
        pixels=np.asarray(Image.open(io.BytesIO(raw)).convert('RGB')).astype(int)
        for color in colors:
            assert np.count_nonzero(np.max(np.abs(pixels-np.array(color)),axis=2)<35)>100
    assert all(current_focus(row) is None for row in project.data['photos'].values())
    web=project.create_web_submission(task,[batch['id']])
    assert web['focus_photo_ids']==[] and web['focus_image_map']=={} and len(requests)==1

    project.confirm(photo_id(assets['A']),5,1)
    rows={row['filename']:row for row in json.loads(project.export_final().read_text(encoding='utf-8'))['photos']}
    assert rows['A.png']['rating']==5 and rows['A.png']['decision_source']=='manual'
    assert rows['B.png']['rating']==4 and rows['B.png']['decision_source']=='ai'
    assert 'rating' not in rows['C.png'] and rows['C.png']['decision_source']=='local'

    # Explicit technical review is optional and goes through actual image prep,
    # strict parser and source-bound cache; only its HTTP answer is simulated.
    assets['B'].ai_focus_result=ai_focus.review_focus(assets['B'],crops,PROFILE,workspace/'technical-cache')
    project.refresh(result.assets,crops)
    assert current_focus(project.data['photos'][photo_id(assets['B'])]) is not None
    select()
    before_change=len(requests)
    old_focus=copy.deepcopy(assets['B'].ai_focus_result)
    crops.photos[crops.key(assets['B'])]['manual_face']=[.25,.20,.4,.5]
    confirmed_settings(assets['B'],crops)
    assets['B'].ai_focus_dirty=True
    result=start_job(source,workspace,options,crops,result=result,mode='rescan').run(
        options,crops,Event(),None,focus_profile=PROFILE)
    assets={a.stem:a for a in result.assets}
    project.refresh(result.assets,crops)
    assert len(requests)==before_change, 'Crop rescan must not automatically pay for AI'
    a,b=(project.data['photos'][photo_id(assets[name])] for name in ('A','B'))
    assert b['ai_focus_result']==old_focus and current_focus(b) is None
    assert current_selection(a) is None and current_selection(b) is None
    assert project.effective_decision(photo_id(assets['A']))['rating']==5
    assert project.effective_decision(photo_id(assets['B']))['rating'] is None
    assert b['history'], 'Previous accepted selection answers must survive'

    # Re-review explicitly, then split groups: single-photo technical AI stays
    # valid while both old/new group selection suggestions become historical.
    assets['B'].ai_focus_result=ai_focus.review_focus(assets['B'],crops,PROFILE,workspace/'technical-cache')
    assets['B'].ai_focus_stale=False
    project.refresh(result.assets,crops)
    select()
    before_group=len(requests)
    assets['B'].group_id=max(a.group_id for a in result.assets)+1
    persist_manual_groups(result)
    project.refresh(result.assets,crops)
    assert len(requests)==before_group
    a,b=(project.data['photos'][photo_id(assets[name])] for name in ('A','B'))
    assert current_selection(a) is None and current_selection(b) is None
    # The checkbox is off: face-only scope can be clear without inventing limb clarity.
    focus = current_focus(b)
    assert focus['status']=='clear'
    assert focus['scope']['enabled_parts']==['face']
    assert all(part['part']=='face' for person in focus['participants'] for part in person['parts'])
    assert len(b['history'])>=2 and b['focus_history']==[old_focus]
    rows={row['filename']:row for row in json.loads(project.export_final().read_text(encoding='utf-8'))['photos']}
    assert rows['A.png']['rating']==5 and rows['A.png']['pick_status']==1
    assert 'rating' not in rows['B.png'] and rows['B.png']['decision_source']=='ai'
    assert 'rating' not in rows['C.png'] and rows['C.png']['decision_source']=='local'
    assert original=={p:p.read_bytes() for p in original}
    assert 'synthetic-key-never-persist' not in project.path.read_text(encoding='utf-8')
