"""Synthetic pixels/opinions only; no Windows Tk/RAW accuracy claim."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import blind_annotation as ba


def fixture(tmp_path):
    path = tmp_path / '中文 空格' / 'same.png'
    path.parent.mkdir()
    Image.new('RGB', (41, 31), 'red').save(path)
    samples = {'schema_version': ba.oe.SCHEMA, 'kind': 'samples', 'dataset_id': 'synthetic',
               'dataset_version': '1', 'manifest_version': '1', 'guideline_version': 'guide-1',
               'evaluation_kind': 'synthetic', 'rows': [{'asset_id':'asset-1', 'sample_version':'1',
                'source_authorization':'generated synthetic test image', 'evaluation_source_id':'source-1',
                'capture_id':'capture1', 'variant_family_id':'variant1', 'comparison_group_id':'comparison1',
                'scene_family_id':'scene1', 'split':'development', 'scene_type':'landscape',
                'historical_exposure':'synthetic generated for tests', 'person_ids':[], 'session_id':None, 'burst_id':None,
                'sources':[{'source_id':'source-1', 'sha256':ba.file_hash(path),'format':'PNG',
                 'dataset_id':'synthetic','dataset_version':'1','original_file_id':'generated-1',
                 'original_sha256':ba.file_hash(path),'width':41,'height':31,'preprocessing_history':[]}]}],
               'dataset_registry':[{'dataset_id':'synthetic','dataset_version':'1','source_url':'generated://test',
                 'license':'synthetic test only','attribution':'test generator','authorization':'generated test pixels',
                 'authorization_confirmed':True,'historical_exposure':'generated here'}]}
    paths = {'schema_version':'real-sample-path-map-v1','manifest_sha256':ba.oe.canonical_hash(samples),
             'bindings':{'asset-1':{'primary_path':str(path),'sources':{'source-1':str(path)}}}}
    return samples, paths, path


def session(tmp_path):
    samples, paths, source = fixture(tmp_path)
    return ba.AnnotationSession(samples, paths, tmp_path/'blind'), samples, paths, source


def viewed(s):
    image, info = s.open_image('asset-1')
    image.close()
    return info


def opinion():
    return {'technical_label':'clear','culling_label':'K','final_selection':'undecided',
            'evidence_adequacy':'adequate','reason':'SYNTHETIC opinion, not a human study',
            'aesthetic_preference':'independently recorded preference', 'regions':[
                {'region_id':'r1','role':'landscape_subject','required':True,'visibility':'visible',
                 'technical_label':'clear','evidence_adequacy':'adequate',
                 'coordinate_space':'oriented_native_decoded_pixels','box':[1,1,20,20]}]}


def test_save_resume_export_native_source(tmp_path):
    s,samples,paths,source=session(tmp_path)
    before=source.read_bytes()
    info=viewed(s)
    assert info['decoded_dimensions']==[41,31]
    assert info['decode']['source_kind']=='standard_image'
    s.save('asset-1','synthetic-rater',opinion(),submit=True)
    reopened=ba.AnnotationSession(samples,paths,s.directory,resume=True)
    assert reopened.latest('asset-1','synthetic-rater')['technical_label']=='clear'
    output=reopened.export(tmp_path/'annotations.json')
    assert output['expert_consensus'] is False
    assert output['rows'][0]['original_ratings'][0]['display_evidence']['sha256']==ba.file_hash(source)
    assert source.read_bytes()==before


def test_draft_never_truth(tmp_path):
    s,*_=session(tmp_path); viewed(s)
    s.save('asset-1','test',{'regions':[]},submit=False)
    assert s.export(tmp_path/'labels.json')['rows']==[]


def test_explicit_submit_and_regions(tmp_path):
    s,*_=session(tmp_path); viewed(s)
    with pytest.raises(ValueError,match='explicit technical_label'):
        s.save('asset-1','test',{'regions':[]},submit=True)
    value=opinion();value['regions']=[]
    with pytest.raises(ValueError,match='inspected region'):
        s.save('asset-1','test',value,submit=True)


def test_no_label_without_view(tmp_path):
    s,*_=session(tmp_path)
    with pytest.raises(ValueError,match='open the declared source'):
        s.save('asset-1','test',opinion(),submit=True)


def test_input_modified_after_view_refused(tmp_path):
    s,_,_,source=session(tmp_path);viewed(s)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='content changed'):
        s.save('asset-1','test',opinion(),submit=True)
    assert s.state['revision']==0


def test_changed_manifest_and_guideline_refused(tmp_path):
    s,samples,paths,_=session(tmp_path)
    changed=deepcopy(samples);changed['guideline_version']='new'
    paths['manifest_sha256']=ba.oe.canonical_hash(changed)
    with pytest.raises(ValueError,match='manifest mismatch'):
        ba.AnnotationSession(changed,paths,s.directory,resume=True)


def test_existing_output_requires_resume(tmp_path):
    s,samples,paths,_=session(tmp_path)
    with pytest.raises(ValueError,match='already exists'):
        ba.AnnotationSession(samples,paths,s.directory)


def test_stale_writer_preserves_original(tmp_path):
    s,samples,paths,_=session(tmp_path)
    other=ba.AnnotationSession(samples,paths,s.directory,resume=True)
    viewed(s);viewed(other)
    s.save('asset-1','one',opinion(),submit=True)
    before=s.journal.read_bytes()
    with pytest.raises(ValueError,match='another process'):
        other.save('asset-1','two',opinion(),submit=True)
    assert before==s.journal.read_bytes()


def test_write_failure_keeps_previous_bytes(tmp_path,monkeypatch):
    s,*_=session(tmp_path);viewed(s)
    s.save('asset-1','one',opinion(),submit=True)
    before=s.journal.read_bytes()
    def fail(*args):raise OSError('synthetic disk failure')
    monkeypatch.setattr(ba,'atomic_json',fail)
    with pytest.raises(OSError,match='disk failure'):
        s.save('asset-1','one',opinion(),submit=True)
    assert before==s.journal.read_bytes()
    assert s.state['revision']==1


def test_lock_conflict(tmp_path):
    s,*_=session(tmp_path);viewed(s)
    with ba.operation(s.directory):
        with pytest.raises(RuntimeError):s.save('asset-1','one',opinion(),submit=True)
    assert s.state['revision']==0


def test_disagreement_and_explicit_adjudication(tmp_path):
    s,*_=session(tmp_path);viewed(s)
    s.save('asset-1','rater1',opinion(),submit=True)
    second=opinion();second['technical_label']='borderline';second['culling_label']='U'
    s.save('asset-1','rater2',second,submit=True)
    result=s.export(tmp_path/'unresolved.json')['rows'][0]
    assert result['unresolved_disagreement'] and result['technical_label'] is None
    s.adjudicate('asset-1','reviewer',opinion(),'explicit synthetic arbitration')
    result=s.export(tmp_path/'resolved.json')['rows'][0]
    assert not result['unresolved_disagreement'] and result['technical_label']=='clear'
    assert len(result['original_ratings'])==2
    assert not result['expert_consensus']
    # A later raw opinion invalidates earlier arbitration, not silently replaced.
    s.save('asset-1','rater2',second,submit=True)
    assert s.export(tmp_path/'new.json')['rows'][0]['unresolved_disagreement']


def test_other_rater_opinion_not_shown_by_latest(tmp_path):
    s,*_=session(tmp_path);viewed(s)
    s.save('asset-1','rater1',opinion(),submit=True)
    assert s.latest('asset-1','rater2') is None


def test_no_prediction_or_external_scores_in_display(tmp_path):
    s,samples,paths,source=session(tmp_path)
    samples['rows'][0]['ai_temporary_labels']={'secret':'REJECT'}
    samples['rows'][0]['MOS']=1
    paths['manifest_sha256']=ba.oe.canonical_hash(samples)
    blind=ba.AnnotationSession(samples,paths,tmp_path/'blind2')
    info=viewed(blind)
    assert 'REJECT' not in json.dumps(info) and 'MOS' not in json.dumps(info)
    assert 'prediction' not in info
    assert 'subject_confirmation' not in blind.export(tmp_path/'empty.json')


def test_only_explicit_member_decoded(tmp_path,monkeypatch):
    s,*_=session(tmp_path)
    from ai_cull_assistant import image_decode
    original=image_decode.load_standard_image
    opened=[]
    def tracked(path):opened.append(str(path));return original(path)
    monkeypatch.setattr(image_decode,'load_standard_image',tracked)
    viewed(s)
    assert opened==[s.paths['bindings']['asset-1']['sources']['source-1']]


@pytest.mark.parametrize('field,value', [('role','other'),('visibility','guess'),('required',None),('coordinate_space','unknown')])
def test_invalid_region_contract(field,value):
    x=opinion();x['regions'][0][field]=value
    with pytest.raises(ValueError):ba.validate_opinion(x,complete=True)


def test_export_refuses_overwrite(tmp_path):
    s,*_=session(tmp_path)
    target=tmp_path/'annotations.json';target.write_text('preserve',encoding='utf-8')
    with pytest.raises(ValueError,match='exists'):s.export(target)
    assert target.read_text('utf-8')=='preserve'


def test_unauthorized_source_rejected_before_read(tmp_path,monkeypatch):
    samples,paths,_=fixture(tmp_path)
    samples['dataset_registry'][0]['authorization_confirmed']=False
    paths['manifest_sha256']=ba.oe.canonical_hash(samples)
    def forbidden(*args):raise AssertionError('must not read unapproved pixels')
    monkeypatch.setattr(ba.inputs,'hash_file',forbidden)
    with pytest.raises(ValueError,match='authorization_not_confirmed'):
        ba.AnnotationSession(samples,paths,tmp_path/'blind')


def test_region_outside_native_dimensions_rejected(tmp_path):
    s,*_=session(tmp_path);viewed(s)
    value=opinion();value['regions'][0]['box']=[0,0,100,100]
    with pytest.raises(ValueError,match='outside native'):
        s.save('asset-1','one',value,submit=True)


def test_embedded_preview_cannot_claim_native100(tmp_path,monkeypatch):
    s,*_=session(tmp_path)
    from ai_cull_assistant import image_decode
    original=image_decode.load_standard_image
    def low_resolution(path):
        image=original(path)
        image.info['decode_evidence']['full_resolution']=False
        return image
    monkeypatch.setattr(image_decode,'load_standard_image',low_resolution)
    with pytest.raises(ValueError,match='full-resolution'):
        s.open_image('asset-1')
    assert not s.viewed


def test_cancel_without_save_leaves_journal_unchanged(tmp_path):
    s,*_=session(tmp_path)
    before=s.journal.read_bytes();viewed(s)
    # Inspection alone and window cancellation do not author human truth.
    assert s.journal.read_bytes()==before
    assert s.export(tmp_path/'empty.json')['rows']==[]


def test_output_inside_photo_directory_refused(tmp_path):
    samples,paths,source=fixture(tmp_path)
    with pytest.raises(ValueError,match='input directory'):
        ba.AnnotationSession(samples,paths,source.parent/'blind')
    assert not (source.parent/'blind').exists()


def test_output_inside_active_workspace_refused(tmp_path):
    samples,paths,_=fixture(tmp_path)
    workspace=tmp_path/'workspace';workspace.mkdir()
    sentinel=workspace/'scan-session.json';sentinel.write_text('preserve',encoding='utf-8')
    with pytest.raises(ValueError,match='active/existing workspace'):
        ba.AnnotationSession(samples,paths,workspace/'blind')
    assert sentinel.read_text('utf-8')=='preserve'


def test_export_into_photo_or_active_directory_refused(tmp_path):
    s,_,_,source=session(tmp_path)
    with pytest.raises(ValueError,match='input directory'):s.export(source.parent/'labels.json')
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'processing-settings.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='active/existing workspace'):s.export(workspace/'labels.json')
