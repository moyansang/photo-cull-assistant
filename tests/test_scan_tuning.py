from ai_cull_assistant.scan_tuning import ScanHistory
from ai_cull_assistant.scan_resources import ResourceSnapshot


def test_history_remembers_only_matching_hardware_and_mode(tmp_path):
    path=tmp_path/'tuning.json'
    hardware=ResourceSnapshot(16,16*1024**3,8*1024**3,100)
    history=ScanHistory(hardware,False,path)
    assert history.preferred() is None
    history.save(4)
    assert ScanHistory(hardware,False,path).preferred()==4
    assert ScanHistory(hardware,True,path).preferred() is None
    assert ScanHistory(ResourceSnapshot(24,32*1024**3,20*1024**3,100),False,path).preferred() is None
    path.write_text('broken')
    assert history.preferred() is None
