"""Default preset names survive add-on replacement; session values do not."""
import json
import os
import tempfile
import bpy

_cache = None
_cache_path = None


def records():
    global _cache, _cache_path
    path = os.path.join(bpy.utils.user_resource('CONFIG'), 'mmd_tools_public', 'default_operator_presets.json')
    if _cache is None or path != _cache_path:
        _cache_path = path
        try:
            with open(path, encoding='utf8') as stream:
                _cache = json.load(stream)
            if not isinstance(_cache, dict):
                _cache = {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save(operator, name, code=None):
    data = records()
    entry = dict(data.get(operator, {}))
    if entry.get('name') != name:
        entry = {'name': name}
    if code is not None:
        entry['code'] = code
    if data.get(operator) == entry:
        return
    data[operator] = entry
    folder = os.path.dirname(_cache_path)
    try:
        os.makedirs(folder, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, _cache_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as exc:
        print('MMD Tools: 无法保存默认预设选择:', exc)


def source(operator, name):
    from .operators.fileio import get_preset_directories
    for folder in get_preset_directories(operator):
        path = os.path.join(folder, name + '.py')
        if os.path.isfile(path):
            with open(path, encoding='utf-8-sig') as stream:
                return stream.read()
    saved = records().get(operator, {})
    return saved.get('code') if saved.get('name') == name else None


def callbacks(operator, property_name):
    def get(self):
        from .preferences import get_preset_items_for_operator
        items = get_preset_items_for_operator(operator)
        saved = records().get(operator)
        if saved is None:
            # Public installs start at built-in defaults. Never import the custom
            # edition's remembered selections; user-created presets remain selectable.
            name = '__DEFAULT__'
        else:
            name = saved.get('name', '__DEFAULT__')
        return next((i for i,item in enumerate(items) if item[0] == name), 0)

    def set(self, value):
        from .preferences import get_preset_items_for_operator
        name = get_preset_items_for_operator(operator)[value][0]
        save(operator, name, source(operator, name) if name != '__DEFAULT__' else None)
    return get, set
