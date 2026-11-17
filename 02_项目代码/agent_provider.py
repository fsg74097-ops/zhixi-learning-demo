"""Small JSON provider adapter with fixed endpoint and bounded output."""
import json
import os
import time
import urllib.request

from deepseek_client import ENDPOINT, ExplanationError, _fetch, _safe_token, _safe_usage
from step_blind_review import _object, _bad_constant


class DeepSeekProvider:
    mode = 'REAL_API'

    def __init__(self, api_key=None, model='deepseek-flash'):
        self._key = os.environ.get('DEEPSEEK_API_KEY', '') if api_key is None else api_key
        self.model = model

    @property
    def configured(self):
        return isinstance(self._key, str) and bool(self._key.strip())

    def call(self, purpose, payload, *, system, max_tokens=1800):
        if not self.configured:
            raise ValueError('MODEL_NOT_CONFIGURED')
        key = self._key.strip()
        if '\n' in key or '\r' in key or self.model not in {'deepseek-flash','deepseek-v4-pro'}:
            raise ValueError('INVALID_CONFIGURATION')
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(content.encode('utf-8')) > 196608 or key in content:
            raise ValueError('INVALID_MODEL_INPUT')
        request_body = {
            'model': self.model, 'stream': False, 'thinking': {'type':'disabled'},
            'max_tokens': min(max_tokens, 2400), 'response_format': {'type':'json_object'},
            'messages': [{'role':'system','content':system}, {'role':'user','content':content}],
        }
        request = urllib.request.Request(ENDPOINT, data=json.dumps(request_body).encode('utf-8'),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        started = time.perf_counter()
        raw = _fetch(request, None, {'purpose':purpose,'model':self.model})
        if key in json.dumps(raw, ensure_ascii=False):
            raise ValueError('SENSITIVE_OUTPUT_REJECTED')
        try:
            choice = raw['choices'][0]
            if choice['finish_reason'] != 'stop':
                raise ValueError('INCOMPLETE_RESPONSE')
            data = json.loads(choice['message']['content'], object_pairs_hook=_object, parse_constant=_bad_constant)
            if not isinstance(data, dict):
                raise ValueError('SCHEMA_INVALID')
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('SCHEMA_INVALID') from error
        return {'data':data, 'mode':self.mode, 'metadata':{
            'purpose':purpose,'model':_safe_token(raw.get('model'), key),
            'request_id':_safe_token(raw.get('id'), key), 'usage':_safe_usage(raw.get('usage')),
            'duration_ms':round((time.perf_counter()-started)*1000,2),
        }}
