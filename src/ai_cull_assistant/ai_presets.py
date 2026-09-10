"""Official vision presets, verified against provider documentation on 2026-09-11."""
from copy import deepcopy

PRESETS = {
    'qwen-vl-plus': dict(
        name='阿里百炼·北京 / Qwen3-VL-Plus',
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        model='qwen3-vl-plus', timeout=300, max_tokens=8192,
        credential_group='dashscope-beijing',
        note='使用阿里百炼北京地域 API Key。Plus 与 Flash 可复用已保存的同地域密钥。',
        request_options={'enable_thinking': False},
        source='https://help.aliyun.com/zh/model-studio/vision'),
    'qwen-vl-flash': dict(
        name='阿里百炼·北京 / Qwen3-VL-Flash',
        base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
        model='qwen3-vl-flash', timeout=300, max_tokens=8192,
        credential_group='dashscope-beijing',
        note='使用阿里百炼北京地域 API Key。Plus 与 Flash 可复用已保存的同地域密钥。',
        request_options={'enable_thinking': False},
        source='https://help.aliyun.com/zh/model-studio/vision'),
    'glm-vision-flash': dict(
        name='智谱 / GLM-4.6V-Flash',
        base_url='https://open.bigmodel.cn/api/paas/v4',
        model='glm-4.6v-flash', timeout=300, max_tokens=8192,
        credential_group='bigmodel',
        note='使用智谱开放平台 API Key；可用额度及调用限流以你的账户为准。',
        request_options={},
        source='https://docs.bigmodel.cn/cn/guide/models/free/glm-4.6v-flash'),
    'openai-mini': dict(
        name='OpenAI / GPT-5.4 mini',
        base_url='https://api.openai.com/v1',
        model='gpt-5.4-mini', timeout=300, max_tokens=8192,
        credential_group='openai', token_parameter='max_completion_tokens',
        note='使用 OpenAI API Key，并确保账户有模型权限。预设自动适配输出长度参数。',
        request_options={'reasoning_effort': 'none'},
        source='https://developers.openai.com/api/docs/models/gpt-5.4-mini'),
    'deepseek-vision': dict(
        name='DeepSeek / Flash（支持图片）',
        base_url='https://api.deepseek.com',
        model='deepseek-flash', timeout=300, max_tokens=8192,
        credential_group='deepseek', max_request_bytes=48*1024*1024,
        note='使用 DeepSeek API Key。Flash 支持图片；预设关闭思考模式。',
        request_options={'thinking': {'type': 'disabled'}},
        source='https://api-docs.deepseek.com/zh-cn/guides/vision/'),
}


def preset_profile(preset_id):
    preset = PRESETS[preset_id]
    result = {key: preset[key] for key in ('name','base_url','model','timeout','max_tokens')}
    result['preset_id'] = preset_id
    return result


def matching_preset(profile):
    preset = PRESETS.get(profile.get('preset_id'))
    if not preset:
        return None
    url = str(profile.get('base_url', '')).strip().rstrip('/')
    if url.endswith('/chat/completions'):
        url = url[:-len('/chat/completions')]
    if url != preset['base_url'] or str(profile.get('model', '')).strip() != preset['model']:
        return None
    return deepcopy(preset)
