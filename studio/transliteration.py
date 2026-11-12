"""Reviewable Roman Telugu -> Telugu conversion, independent of narration billing."""
import json
import re
import uuid

from .config import DATA
from . import store, providers


def convert(text, provider):
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError('Enter 1–3,000 characters of Roman Telugu for this scene.')
    if provider not in ('sarvam', 'gemini'):
        raise ValueError('Choose Gemini or Sarvam for script conversion.')
    text = text.strip()
    cfg = providers.settings()
    key = store.digest({'transliteration': 1, 'provider': provider, 'text': text,
                        'model': cfg.get('gemini_model') if provider == 'gemini' else 'sarvam'})
    cache = DATA / 'cache' / (key + '.transliteration.json')
    if cache.is_file():
        return {**json.loads(cache.read_text('utf-8')), 'cached': True}
    cfg = providers.online()
    if provider == 'sarvam':
        if not cfg.get('sarvam_key'):
            raise ValueError('Add a Sarvam key in Settings for conversion, or paste Telugu text manually.')
        # Sarvam accepts at most 1,000 characters. Split only at whitespace so
        # names and words are never silently truncated. Cache each paid chunk.
        parts = re.findall(r'\S+\s*', text)
        chunks, chunk = [], ''
        for part in parts:
            if len(part) > 1000:
                raise ValueError('A word exceeds the conversion limit. Add a space or split the scene.')
            if len(chunk) + len(part) > 1000:
                chunks.append(chunk.strip())
                chunk = ''
            chunk += part
        if chunk:
            chunks.append(chunk.strip())
        output = []
        for chunk in chunks:
            chunk_key = store.digest({'sarvam-transliteration': 1, 'text': chunk})
            chunk_path = DATA / 'cache' / (chunk_key + '.transliteration-part.json')
            if chunk_path.is_file():
                output.append(json.loads(chunk_path.read_text('utf-8')))
                continue
            if not providers.acquire_rate_slot('sarvam'):
                raise ValueError('Sarvam rate limit reached. Try again shortly.')
            raw = providers.request('POST', 'https://api.sarvam.ai/transliterate',
                                    headers={'api-subscription-key': cfg['sarvam_key']},
                                    json={'input': chunk, 'source_language_code': 'en-IN',
                                          'target_language_code': 'te-IN', 'numerals_format': 'international'}).json()
            value = raw.get('transliterated_text')
            _validate(value)
            _write(chunk_path, value)
            providers.record_usage('sarvam', estimated_units=len(chunk), actual_units=len(chunk))
            output.append(value)
        native = ' '.join(output)
    else:
        raw, _ = providers.gemini('Transliterate this Romanized Telugu into Telugu Unicode. '
                                 'Preserve pronunciation, meaning, names, facts and sentence order. '
                                 'Do not translate into English, rewrite, summarize or add claims. '
                                 'Return JSON with exactly one key "telugu". Input JSON: ' + json.dumps(text), fast=True)
        try:
            native = json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip())['telugu']
        except (ValueError, KeyError, TypeError):
            raise ValueError('Conversion returned invalid text. Your script has not changed.') from None
    _validate(native)
    result = {'telugu': native.strip(), 'roman': text, 'provider': provider, 'cached': False}
    _write(cache, result)
    return result


def _validate(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 3000 or not re.search(r'[\u0c00-\u0c7f]', text):
        raise ValueError('Conversion did not return usable Telugu. Edit or split this scene and retry.')


def _write(path, value):
    temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), 'utf-8')
    temp.replace(path)
