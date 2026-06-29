"""
Shared AI API client for OpenRouter chat calls.
"""

import requests


DEFAULT_OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'


def _log(log_func, message):
    if log_func:
        log_func(message)


def get_openrouter_model(config):
    """Return the configured primary model."""
    primary = str(config.get('openrouter_model', '')).strip()
    if not primary:
        raise RuntimeError('OpenRouter 模型未配置')
    return primary


def call_openrouter_chat(config, prompt, max_tokens=4000, timeout=180, log_func=None):
    """
    Call the configured chat completions endpoint once with the primary model.

    Returns a dict compatible with OpenRouter's chat completion response, with
    '_used_model' added for logging.
    """
    api_key = config.get('openrouter_api_key', '')
    if not api_key:
        raise RuntimeError('OpenRouter API Key 未配置')

    model = get_openrouter_model(config)
    base_url = config.get('openrouter_base_url') or DEFAULT_OPENROUTER_URL

    try:
        response = requests.post(
            base_url,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens,
            },
            timeout=timeout,
        )
        data = response.json()
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f'AI 请求超时: {model}') from e
    except Exception as e:
        raise RuntimeError(f'AI 请求异常: {model}: {e}') from e

    if 'error' in data:
        err = data['error']
        msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f'AI 返回错误: {model}: {msg[:200]}')

    content = data.get('choices', [{}])[0].get('message', {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f'AI 返回内容为空: {model}')

    data['_used_model'] = model
    return data
