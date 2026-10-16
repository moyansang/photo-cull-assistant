"""Exercise result summaries/filters without opening a Tcl/Tk window."""
from types import SimpleNamespace

from ai_cull_assistant.ai_review_ui import ReviewDialog


def test_web_summary_ignores_stale_technical_ai_and_respects_manual_priority():
    stale={'technical_fingerprint':'new','focus_fingerprint':'old','focus_stale':True,
           'ai_focus_result':{'status':'blur','source':'web','reason':'old blur'},
           'focus_review':True,'clarity_evidence':{'state':'clear'}}
    manual={'technical_fingerprint':'p','focus_fingerprint':'p',
            'ai_focus_result':{'status':'blur','source':'web','reason':'blur'},
            'final':{'confirmed':True,'rating':5,'pick_status':1},'focus_review':False}
    pending={'clarity_evidence':{'state':'uncertain'}}
    rows={'stale':stale,'manual':manual,'pending':pending}
    messages=[]
    fake=SimpleNamespace(_photos=lambda:rows,_on_log=messages.append)
    ReviewDialog._log_web_focus(fake,{'photo_ids':list(rows)})
    assert messages==['网页回答已处理：有效 AI 技术复核 0 张；当前推荐弃置 0 张；清晰度待确认 2 张。']


def test_headless_review_filter_uses_effective_current_decision():
    photo={'fingerprint':'g','stale':True,
           'ai':{'fingerprint':'old','rating':5,'suggest_reject':True},
           'focus_stale':True,'focus_review':True,'clarity_evidence':{'state':'clear'}}
    fake=SimpleNamespace(filter_var=SimpleNamespace(get=lambda:'AI 建议弃置'))
    assert not ReviewDialog._matches_filter(fake,photo)
    fake.filter_var.get=lambda:'4～5 星'
    assert not ReviewDialog._matches_filter(fake,photo)
    fake.filter_var.get=lambda:'清晰度待确认'
    assert ReviewDialog._matches_filter(fake,photo)  # old clear has no current binding
    fake.filter_var.get=lambda:'结果已过时'
    assert ReviewDialog._matches_filter(fake,photo)
    photo['final']={'confirmed':True,'rating':5,'pick_status':1}
    from ai_cull_assistant.lightroom_results import effective_decision
    assert effective_decision(photo)['focus_review'] is False
    fake.filter_var.get=lambda:'4～5 星'
    assert ReviewDialog._matches_filter(fake,photo)
