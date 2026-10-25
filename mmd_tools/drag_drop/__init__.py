"""Backport MMD Tools v4.5.14's file-drop interaction, not its import engine.

GPL-3.0-or-later. See SOURCE_NOTES.md for upstream source references.
The installed MMD Tools defines settings, drawing, poll and execution.
No patches are made to its registered classes or files.
"""

import atexit
from collections import deque
import os
import sys
import traceback
import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

FORMATS = {'.pmx': 'import_model', '.pmd': 'import_model',
           '.vmd': 'import_vmd', '.vpd': 'import_vpd'}
_bridge = None
_routes = {}
_incoming = deque()
_jobs = deque()
_busy = False
_classes = []
_status = '等待初始化'
_session_settings = {}


def native_operator(name):
    return getattr(bpy.ops.mmd_tools, name)


def upstream_area(area):
    """Blender 4.5 bpy_extras.io_utils.poll_file_object_drop behavior."""
    return bool(area and (area.type == 'VIEW_3D' or
                (area.type == 'OUTLINER' and area.spaces.active.display_mode == 'VIEW_LAYER')))


def stable_branch_enabled():
    return (hasattr(bpy.types, 'UD36_PT_Panel') or
            hasattr(bpy.types, 'MMD_DROP36_PT_Panel'))


def refresh_routes():
    global _routes
    from . import win_drop as win
    routes = {}
    if _bridge:
        for hwnd in _bridge.targets:
            rect = win.W.RECT()
            win.getrect(hwnd, win.C.byref(rect))
            windows = [w for w in bpy.context.window_manager.windows
                       if abs(w.width - rect.right) <= 2 and abs(w.height - rect.bottom) <= 2]
            if len(windows) != 1:
                continue
            window = windows[0]
            regions = [(a.x, a.y, a.width, a.height, a.as_pointer())
                       for a in window.screen.areas if upstream_area(a)]
            routes[hwnd] = (window.as_pointer(), rect.bottom, regions)
    _routes = routes


def route_at(hwnd, x, y):
    route = _routes.get(hwnd)
    if route:
        window, height, regions = route
        for ax, ay, aw, ah, area in regions:
            if ax <= x < ax + aw and ay <= height - y < ay + ah:
                return window, area
    return None


def accept(hwnd, paths, x, y):
    # Native callbacks must not call bpy. Mixed unsupported batches are handed
    # back to Blender as a whole, rather than silently dropping their members.
    return bool(paths and route_at(hwnd, x, y) and
                all(os.path.splitext(path)[1].lower() in FORMATS for path in paths))


def receive(hwnd, paths, x, y):
    route = route_at(hwnd, x, y)
    if route:
        _incoming.append((list(paths), route))


def context_for(route):
    for window in bpy.context.window_manager.windows:
        if window.as_pointer() == route[0]:
            for area in window.screen.areas:
                if area.as_pointer() == route[1] and upstream_area(area):
                    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                    if region:
                        return dict(window=window, area=area, region=region)
    return None


def group_jobs(paths, route):
    """Batch consecutive files sharing importer and directory, preserving order."""
    batches = []
    for path in paths:
        name = FORMATS.get(os.path.splitext(path)[1].lower())
        if name is None:
            raise ValueError('此分支仅处理 PMX、PMD、VMD、VPD：' + path)
        key = (name, os.path.normcase(os.path.dirname(path)))
        if batches and batches[-1][0] == key:
            batches[-1][1].append(path)
        else:
            batches.append((key, [path]))
    return [(key[0], files, route) for key, files in batches]


def file_browser_open():
    return any(a.type == 'FILE_BROWSER' for w in bpy.context.window_manager.windows
               for a in w.screen.areas)


def show_error(message):
    def draw(self, context):
        self.layout.label(text=message)
    bpy.context.window_manager.popup_menu(draw, title='MMD 拖放', icon='ERROR')


def tick():
    global _busy, _status, _bridge
    try:
        addon = bpy.context.preferences.addons.get('mmd_tools')
        if addon and not addon.preferences.enable_mmd_drag_drop:
            _status = 'MMD 拖放已关闭'
            return 0.25
        if stable_branch_enabled():
            # If the other branch was enabled later, keep our forwarding chain
            # intact but stop accepting drops. Closing out of order could
            # unregister the other bridge's target.
            _routes.clear()
            _incoming.clear()
            _jobs.clear()
            _status = '请停用旧独立拖放插件并重启 Blender'
            return 0.25
        if _bridge is None:
            from .win_drop import Bridge
            _bridge = Bridge(accept, receive)
        if _bridge:
            _status = '内置拖放就绪' if _status.startswith('请停用') else _status
            _bridge.sync()
            refresh_routes()
        while _incoming:
            paths, route = _incoming.popleft()
            _jobs.extend(group_jobs(paths, route))
        if not _busy and not file_browser_open() and _jobs:
            name, paths, route = _jobs.popleft()
            override = context_for(route)
            if not override:
                _status = '目标窗口或视图已关闭'
                return 0.25
            with bpy.context.temp_override(**override):
                # The upstream importer only requires a selection for VMD/VPD.
                # No object normalization, content detection or target picker.
                op = native_operator(name)
                if not op.poll():
                    _status = '原导入器当前不可用；VMD/VPD 请先选中接收对象'
                    show_error(_status)
                else:
                    getattr(bpy.ops.mmd_tools_drop, name)(
                        'INVOKE_DEFAULT', filepath=paths[0],
                        directory=os.path.dirname(paths[0]) + os.sep,
                        files=[{'name': os.path.basename(p)} for p in paths])
    except Exception as exc:
        traceback.print_exc()
        _busy = False
        _status = str(exc)
        show_error(_status)
    return 0.25


def property_arguments(instance, operator):
    args = {}
    for prop in operator.get_rna_type().properties:
        key = prop.identifier
        # Collection containers are read-only RNA references, but operator
        # invocation accepts their contents (notably files) as dictionaries.
        if key == 'rna_type' or (prop.is_readonly and key != 'files'):
            continue
        value = getattr(instance, key)
        if prop.type == 'COLLECTION':
            if key == 'files':
                args[key] = [{'name': f.name} for f in value]
        elif prop.type != 'POINTER':
            args[key] = value[:] if getattr(prop, 'is_array', False) else value
    return args


def setting_keys(name):
    """Match the original RNA's session-save rules, excluding path payloads."""
    for prop in native_operator(name).get_rna_type().properties:
        if (prop.identifier not in {'rna_type', 'filepath', 'directory', 'files', 'filter_glob', 'convert_to_60fps'}
                and not prop.is_readonly and not getattr(prop, 'is_skip_save', False)
                and prop.type not in {'COLLECTION', 'POINTER'}):
            yield prop.identifier


def restore_settings(instance, name):
    saved = _session_settings.get(name, {})
    for key in setting_keys(name):
        # Explicit arguments and preset values take precedence over history.
        if key in saved and not instance.properties.is_property_set(key):
            try:
                setattr(instance, key, saved[key])
            except (TypeError, ValueError):
                # Dynamic enum entries can disappear when user dictionaries change.
                pass


def remember_settings(instance, name):
    args = property_arguments(instance, native_operator(name))
    _session_settings[name] = {key: args[key] for key in setting_keys(name) if key in args}


def build_import_class(original, name):
    # Copy deferred property declarations, never modify installed definitions.
    annotations = dict(original.__annotations__)
    annotations['files'] = CollectionProperty(type=bpy.types.OperatorFileListElement,
                                              options={'HIDDEN', 'SKIP_SAVE'})
    annotations['directory'] = StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    annotations['filepath'] = StringProperty(subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    from ..operators.fileio import PreferencesMixin
    preset_property = {"import_model": "default_pmx_import_preset", "import_vmd": "default_vmd_import_preset", "import_vpd": "default_vpd_import_preset"}[name]
    original_draw = getattr(original, 'draw', None)

    @classmethod
    def poll(cls, context):
        try:
            return native_operator(name).poll()
        except (AttributeError, RuntimeError):
            return False

    def invoke(self, context, event):
        global _busy
        _busy = True
        try:
            explicit = self.convert_to_60fps if name == 'import_vmd' and self.properties.is_property_set('convert_to_60fps') else None
            restore_settings(self, name)
            self.load_preferences_on_invoke(context, preset_property)
            if name == 'import_vmd':
                from ..operators.fileio import initialize_vmd_timing
                initialize_vmd_timing(self, context, explicit)
            if self.properties.is_property_set('filepath'):
                if not self.files and self.filepath:
                    # Old VMD/VPD execute iterate files; filepath alone is not enough.
                    self.directory = os.path.dirname(self.filepath) + os.sep
                    self.files.add().name = os.path.basename(self.filepath)
                return context.window_manager.invoke_props_dialog(self, width=460)
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        except Exception:
            _busy = False
            raise

    def draw(self, context):
        if len(self.files) > 1:
            self.layout.label(text='导入 %d 个文件' % len(self.files), icon='IMPORT')
        else:
            self.layout.label(text=os.path.basename(self.filepath), icon='IMPORT')
        if original_draw:
            original_draw(self, context)
        else:
            for prop in native_operator(name).get_rna_type().properties:
                if prop.identifier not in {'rna_type', 'filepath', 'directory', 'files', 'filter_glob'} and not prop.is_hidden:
                    self.layout.prop(self, prop.identifier)

    def execute(self, context):
        global _busy, _status
        try:
            op = native_operator(name)
            result = op('EXEC_DEFAULT', False, **property_arguments(self, op))
            if 'FINISHED' in result:
                remember_settings(self, name)
            _status = ('已导入：' if 'FINISHED' in result else '导入已取消：') + os.path.basename(self.filepath)
            return result
        except Exception as exc:
            traceback.print_exc()
            _status = '原导入器错误：' + str(exc)
            self.report({'ERROR'}, _status)
            return {'CANCELLED'}
        finally:
            _busy = False

    def cancel(self, context):
        global _busy, _status
        _busy = False
        self.restore_preferences_on_cancel()
        _status = '已取消当前导入'

    return type('MMD_TOOLS_DROP_OT_' + name, (bpy.types.Operator, ImportHelper, PreferencesMixin), {
        '__module__': __name__, '__annotations__': annotations,
        'bl_idname': 'mmd_tools_drop.' + name, 'bl_label': original.bl_label,
        'bl_description': original.bl_description, 'bl_options': {'REGISTER', 'UNDO', 'PRESET'},
        'filename_ext': original.filename_ext, 'poll': poll, 'invoke': invoke,
        'draw': draw, 'execute': execute, 'cancel': cancel,
    })


class MMD_TOOLS_DROP_OT_Clear(bpy.types.Operator):
    bl_idname = 'mmd_tools_drop.clear'
    bl_label = '清空剩余队列'

    def execute(self, context):
        _incoming.clear()
        _jobs.clear()
        return {'FINISHED'}





@bpy.app.handlers.persistent
def reset_pending_on_load(_dummy):
    # File loading can destroy a modal dialog without sending cancel().
    global _busy
    _busy = False
    _incoming.clear()
    _jobs.clear()
    _routes.clear()


def shutdown():
    global _bridge
    if _bridge:
        _bridge.close()
        _bridge = None
    _routes.clear()


def register():
    global _bridge, _busy, _status
    if _classes:
        return
    if bpy.app.version[:2] != (3, 6) or sys.platform != 'win32':
        _status = '内置拖放仅支持 Windows / Blender 3.6'
        return
    # Importing its Python module is not enough: require registered operators.
    try:
        import mmd_tools
        from mmd_tools.operators import fileio
        if getattr(mmd_tools, 'BASE_MMD_TOOLS_VERSION', mmd_tools.bl_info['version'])[0] != 2:
            raise RuntimeError('此分支适配 MMD Tools 2.x')
        for name in set(FORMATS.values()):
            native_operator(name).get_rna_type()
    except (ImportError, AttributeError, KeyError) as exc:
        raise RuntimeError('请先启用兼容 Blender 3.6 的 MMD Tools 2.x') from exc
    classes = [build_import_class(original, name) for original, name in (
        (fileio.ImportPmx, 'import_model'), (fileio.ImportVmd, 'import_vmd'), (fileio.ImportVpd, 'import_vpd'))]
    classes += [MMD_TOOLS_DROP_OT_Clear]
    try:
        for cls in classes:
            bpy.utils.register_class(cls)
            _classes.append(cls)
        bpy.app.handlers.load_post.append(reset_pending_on_load)
        _busy = False
        _status = 'MMD Tools %s · 上游拖放交互' % '.'.join(map(str, mmd_tools.bl_info['version']))
        if not bpy.app.background:
            from .win_drop import Bridge
            addon = bpy.context.preferences.addons.get('mmd_tools')
            if not addon or addon.preferences.enable_mmd_drag_drop:
                _bridge = Bridge(accept, receive)
            bpy.app.timers.register(tick, first_interval=1, persistent=True)
            atexit.register(shutdown)
    except Exception:
        unregister()
        raise


def unregister():
    global _busy
    if reset_pending_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_pending_on_load)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    shutdown()
    atexit.unregister(shutdown)
    _incoming.clear()
    _jobs.clear()
    _session_settings.clear()
    _busy = False
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _classes.clear()
