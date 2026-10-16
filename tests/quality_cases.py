"""Explicit synthetic protocol fixtures; no inference or real quality claims."""
import json
from ai_cull_assistant import focus_protocol as protocol
from ai_cull_assistant.primary_quality import confirm_selection, CONFIRMATION_KEY


def confirmed_settings(asset, settings, *, faces=None, bodies=None):
    entry=settings.photos.setdefault(settings.key(asset), {})
    if faces is not None:
        entry['selected_faces']=faces
    elif 'selected_faces' not in entry and 'selected_bodies' not in entry and 'manual_face' not in entry:
        entry['selected_faces']=[[.1,.1,.6,.6]]
    if bodies is not None:
        entry['selected_bodies']=bodies
    entry[CONFIRMATION_KEY]=confirm_selection(asset,settings)
    return settings


def technical_result(asset, settings, status='clear', reason='native detail'):
    """Construct validated test data with explicit current subject/part binding."""
    confirmed_settings(asset,settings)
    scope=protocol.make_scope(asset,settings)
    items=[]
    for subject in scope['subjects']:
        ctx=protocol.make_context(scope,subject['id'],evidence_parts=('face','body'))
        value=protocol.response_template(ctx)
        value['reason']=reason
        for part in value['parts']:
            part.update(visibility='visible' if status!='uncertain' else 'unknown',
                        evidence_sufficient=status!='uncertain',
                        severity={'clear':'clear','blur':'severe','uncertain':'uncertain'}[status],
                        reason=reason,location=part['part'])
        import json
        items.append(protocol.parse_response(json.dumps(value),ctx))
    result=protocol.assemble_result(scope,items)
    result['source']='api'
    from ai_cull_assistant.ai_project import technical_fingerprint
    result['technical_fingerprint']=technical_fingerprint(asset,settings)
    asset.focus_scope=scope
    return result


def protocol_reply(prompt, severity='clear'):
    """Answer only the parts actually offered by the request's native inputs."""
    ranges=json.loads(prompt.split('请求范围：')[1].split('\n返回格式：')[0])
    value=json.loads(prompt.split('返回格式：')[1])
    for part in value['parts']:
        if part['part'] in ranges['evidence_parts']:
            part.update(visibility='visible',severity=severity,evidence_sufficient=True,
                        reason='Controlled native edge evidence',location=part['part'])
        elif part['part'] in ranges['not_visible_parts']:
            part.update(visibility='not_visible')
    return {'text':json.dumps(value)}
