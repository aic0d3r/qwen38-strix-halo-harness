"""Codex model metadata from the client's catalog, never API window guesses."""
import json
from runtime_env import codex_home


def catalog():
    try:
        path = codex_home() / 'models_cache.json'
        if path.stat().st_size > 8 * 1024 * 1024:
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        return [m for m in data.get('models', []) if isinstance(m, dict) and isinstance(m.get('slug'), str)]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def effective_window(model):
    for entry in catalog():
        if entry['slug'] == model:
            try:
                window = int(entry['context_window'])
                percent = int(entry.get('effective_context_window_percent', 100))
                if window > 0 and 0 < percent <= 100:
                    return window * percent // 100
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
    return None


def visible_models():
    return [{'id': m['slug'], 'name': m.get('display_name') or m['slug'],
             'effective_context_window': effective_window(m['slug']),
             'reasoning_efforts': m.get('supported_reasoning_levels', [])}
            for m in catalog() if m.get('visibility') == 'list']
