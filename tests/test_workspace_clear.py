from pathlib import Path

import pytest

from ai_cull_assistant.workspace_clear import clear_workspace


def test_clear_workspace_removes_contents_but_keeps_root_and_originals(tmp_path):
    source=tmp_path/'photos';source.mkdir()
    original=source/'photo.raw';original.write_bytes(b'original')
    program=tmp_path/'program';program.mkdir()
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'previews').mkdir();(workspace/'previews'/'p.jpg').write_bytes(b'preview')
    (workspace/'result.json').write_text('{}')

    clear_workspace(workspace,input_dir=source,program_dir=program,original_paths=[original])

    assert workspace.is_dir() and list(workspace.iterdir()) == []
    assert original.read_bytes() == b'original'


@pytest.mark.parametrize('workspace_kind', ['same','ancestor','child','program'])
def test_clear_workspace_rejects_overlapping_protected_paths(tmp_path, workspace_kind):
    source=tmp_path/'photos';source.mkdir()
    program=tmp_path/'program';program.mkdir()
    if workspace_kind == 'same':
        workspace=source
    elif workspace_kind == 'ancestor':
        workspace=tmp_path
    elif workspace_kind == 'child':
        workspace=source/'generated';workspace.mkdir()
    else:
        workspace=program/'data';workspace.mkdir()
    marker=workspace/'keep.txt';marker.write_text('keep')

    with pytest.raises(ValueError):
        clear_workspace(workspace,input_dir=source,program_dir=program)
    assert marker.read_text() == 'keep'


def test_clear_workspace_rejects_original_nested_in_workspace(tmp_path):
    source=tmp_path/'photos';source.mkdir()
    program=tmp_path/'program';program.mkdir()
    workspace=tmp_path/'workspace';workspace.mkdir()
    original=workspace/'unexpected.raw';original.write_bytes(b'original')

    with pytest.raises(ValueError):
        clear_workspace(workspace,input_dir=source,program_dir=program,original_paths=[original])
    assert original.exists()


def test_clear_workspace_allows_one_managed_workspace_below_program(tmp_path):
    source=tmp_path/'photos';source.mkdir()
    program=tmp_path/'program';program.mkdir()
    workspace=program/'workspaces'/'project-a';workspace.mkdir(parents=True)
    (workspace/'cache.bin').write_bytes(b'x')

    clear_workspace(workspace,input_dir=source,program_dir=program)

    assert workspace.is_dir() and list(workspace.iterdir()) == []
