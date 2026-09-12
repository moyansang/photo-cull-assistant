from types import SimpleNamespace

import pytest

from ai_cull_assistant.app import App


@pytest.mark.parametrize('retry', [True, False])
def test_end_of_focus_run_retries_only_errors_or_skips(retry, monkeypatch):
    calls = []
    job = SimpleNamespace(
        pending_focus_errors=({'filename': 'P1100533.RW2', 'message': 'JSON error'},),
        retry_failed=lambda: calls.append('retry'),
        skip_failed=lambda: calls.append('skip'),
    )
    owner = SimpleNamespace(
        _processing_job=job, _processing_busy=False,
        _start_processing=lambda **kwargs: calls.append(kwargs),
        _log=lambda text: None,
    )
    prompts = []
    def ask(title, text, **kwargs):
        prompts.append(text)
        return retry
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.askyesno', ask)
    App._resolve_focus_errors(owner, job)
    assert calls == ['retry' if retry else 'skip', {'resume': True, 'focus_errors_skipped': not retry}]
    assert 'P1100533.RW2' in prompts[0]
    assert '保留清晰度待确认' in prompts[0]
