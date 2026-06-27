"""
Shared AI API client for OpenRouter chat calls.
"""

import time

import requests


DEFAULT_OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'


def _log(log_func, message):
    if log_func:
        log_func(message)


def _fallback_models(config):
    raw = config.get('openrouter_fallback_models', [])
    if isinstance(raw, str):
        return [m.strip() for m in raw.split(',') if m.strip()]
    if isinstance(raw, list):
        return [str(m).strip() for m in raw if str(m).strip()]
    return []


def get_openrouter_models(config):
    """Return primary model followed by fallback models without duplicates."""
    models = []
    primary = str(config.get('openrouter_model', '')).strip()
    if primary:
        models.append(primary)
    for model in _fallback_models(config):
        if model not in models:
            models.append(model)
    return models


def call_openrouter_chat(config, prompt, max_tokens=4000, retries=2, timeout=180, log_func=None):
    """
    Call OpenRouter chat completions with optional fallback models.

    Returns a dict compatible with OpenRouter's chat completion response, with
    '_used_model' added for logging.
    """
    api_key = config.get('openrouter_api_key', '')
    if not api_key:
        raise RuntimeError('OpenRouter API Key 未配置')

    models = get_openrouter_models(config)
    if not models:
        raise RuntimeError('OpenRouter 模型未配置')

    base_url = config.get('openrouter_base_url') or DEFAULT_OPENROUTER_URL
    last_error = None

    for model_idx, model in enumerate(models):
        for attempt in range(retries):
            try:
                if model_idx > 0 and attempt == 0:
                    _log(log_func, f'  切换备用模型: {model}')

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
                if 'error' in data:
                    err = data['error']
                    msg = err.get('message', '')[:120]
                    code = err.get('code', response.status_code)
                    last_error = f'{model}: {err}'
                    _log(log_func, f'  模型 {model} 尝试{attempt + 1}/{retries} 失败: {msg}')
                    if attempt < retries - 1:
                        time.sleep(30 if code == 429 else 10)
                        continue
                    break

                content = data.get('choices', [{}])[0].get('message', {}).get('content')
                if not isinstance(content, str) or not content.strip():
                    last_error = f'{model}: 模型返回内容为空'
                    _log(log_func, f'  模型 {model} 尝试{attempt + 1}/{retries} 失败: 模型返回内容为空')
                    if attempt < retries - 1:
                        time.sleep(10)
                        continue
                    break

                data['_used_model'] = model
                return data
            except requests.exceptions.Timeout:
                last_error = f'{model}: 请求超时'
                _log(log_func, f'  模型 {model} 尝试{attempt + 1}/{retries} 超时')
                if attempt < retries - 1:
                    time.sleep(10)
                    continue
                break
            except Exception as e:
                last_error = f'{model}: {e}'
                _log(log_func, f'  模型 {model} 尝试{attempt + 1}/{retries} 异常: {e}')
                if attempt < retries - 1:
                    time.sleep(10)
                    continue
                break

    raise RuntimeError(f'OpenRouter 调用失败: {last_error}')
