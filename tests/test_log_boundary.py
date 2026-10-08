import pytest
from ai_cull_assistant.app import App
from ai_cull_assistant.settings import save_values
from ai_cull_assistant.workspace_layout import workspace_path
from ai_cull_assistant.workspace_log import HISTORY_BOUNDARY, DETAIL_PREFIX


def setup_paths(tmp_path, history):
    photos=tmp_path/'photos';photos.mkdir()
    workspace=tmp_path/'workspace'
    save_values(tmp_path/'settings',dict(input=str(photos),workspace=str(workspace)))
    log=workspace_path(workspace,'session.log')
    if history is not None:
        log.parent.mkdir(parents=True,exist_ok=True);log.write_text(history,encoding='utf-8')
    return tmp_path/'settings',log


@pytest.mark.parametrize('history',[None,'','  \n',DETAIL_PREFIX+'hidden detail\n'])
def test_empty_visible_history_has_no_boundary_even_after_repeat(tmp_path,history):
    settings,log=setup_paths(tmp_path,history)
    app=App(settings_dir=settings)
    try:
        app.withdraw();app.update()
        app._restore_session();app.update()
        assert HISTORY_BOUNDARY not in app.log_text.get('1.0','end')
        assert HISTORY_BOUNDARY not in log.read_text('utf-8')
    finally:app._close()


@pytest.mark.parametrize('history',['old message\n','old message'])
def test_boundary_order_repeat_and_persistence_across_reopen(tmp_path,history):
    settings,log=setup_paths(tmp_path,history)
    for iteration in range(2):
        before=log.read_text('utf-8')
        app=App(settings_dir=settings)
        try:
            app.withdraw();app._log(f'new message {iteration}');app.update()
            text=app.log_text.get('1.0','end')
            assert text.startswith(before+('' if before.endswith('\n') else '\n')+HISTORY_BOUNDARY+'\n')
            current=text.split(HISTORY_BOUNDARY,1)[1]
            assert current.index('当前保存设置：') < current.index(f'new message {iteration}')
            app._restore_session();app._restore_session();app.update()
            assert app.log_text.get('1.0','end').count(HISTORY_BOUNDARY)==1
            assert app.log_text.get('1.0','end').count('old message')==1
            assert log.read_text('utf-8').startswith(before)
            assert HISTORY_BOUNDARY not in log.read_text('utf-8')
        finally:app._close()


def test_existing_tail_boundary_is_not_duplicated(tmp_path):
    settings,log=setup_paths(tmp_path,'old message\n'+HISTORY_BOUNDARY+'\n')
    app=App(settings_dir=settings)
    try:
        app.withdraw();app.update()
        assert app.log_text.get('1.0','end').count(HISTORY_BOUNDARY)==1
        assert log.read_text('utf-8').count(HISTORY_BOUNDARY)==1
    finally:app._close()


def test_clear_and_restore_false_keep_display_and_disk_contract(tmp_path):
    settings,log=setup_paths(tmp_path,'old message\n')
    app=App(settings_dir=settings)
    try:
        app.withdraw();app.update();before=log.read_bytes()
        app._clear_log();app._restore_session(restore_log=False);app.update()
        assert HISTORY_BOUNDARY not in app.log_text.get('1.0','end')
        assert log.read_bytes()==before
        app._restore_session();app.update()
        assert app.log_text.get('1.0','end').count(HISTORY_BOUNDARY)==1
    finally:app._close()
