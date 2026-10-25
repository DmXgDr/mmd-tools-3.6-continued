"""Update this fork from its own GitHub Releases, preserving operator IDs."""
import os
import threading
import bpy
from .. import update_service as service


class AddonUpdaterManager:
    _instance = None
    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def init(self, info, init_file):
        self._generation = getattr(self, '_generation', 0) + 1
        self.current = tuple(info['version'])
        self.target = os.path.dirname(os.path.realpath(init_file))
        self.release = None
        self.busy = self.done = self.checked = False
        self.message = self.failure = ''
        self._result = None
        self._active = True

    def candidate_checked(self): return self.checked
    def updated(self): return self.done
    def has_error(self): return bool(self.failure)
    def error(self): return self.failure
    def has_info(self): return bool(self.message)
    def info(self): return self.message
    def latest_version(self): return self.release['tag'] if self.release else ''
    def update_ready(self): return bool(self.release and tuple(self.release['version']) > self.current and not self.done)
    def get_candidate_branch_names(self): return [self.latest_version()] if self.release else []

    def start(self, install, token):
        if self.busy or self.done: return
        self.busy = True
        self.failure = ''
        self.message = '正在下载并校验更新…' if install else '正在检查 GitHub Releases…'
        self._result = None
        generation, current, target = self._generation, self.current, self.target
        def deliver(result):
            if self._active and generation == self._generation: self._result = result
        def worker():
            try:
                release = service.check_release(token)
                if install:
                    if tuple(release['version']) <= current:
                        raise service.UpdateError('没有更新的正式版本')
                    data = service.download_release(release, token)
                    if not self._active or generation != self._generation: return
                    backup = service.install_archive(data, target, release['version'])
                    deliver((True, release, backup))
                else: deliver((True, release, None))
            except Exception as exc:
                deliver((False, None, str(exc) if isinstance(exc, service.UpdateError) else '更新检查失败，请稍后重试'))
        threading.Thread(target=worker, name='MMDReleaseUpdate', daemon=True).start()
        bpy.app.timers.register(self.finish, first_interval=.25)

    def finish(self):
        if not self._active: return None
        if self._result is None: return .25
        ok, release, detail = self._result
        self.busy = False
        if not ok:
            self.failure = detail
            self.message = ''
        else:
            self.release = release
            self.checked = True
            self.done = detail is not None
            self.message = ('已安装 %s，请保存工程后重启 Blender' % release['tag']) if self.done else (
                ('发现更新：' + release['tag']) if self.update_ready() else '当前已是最新正式版本')
            if self.done:
                from .. import drag_drop
                drag_drop.shutdown()
                if bpy.app.timers.is_registered(drag_drop.tick): bpy.app.timers.unregister(drag_drop.tick)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas: area.tag_redraw()
        return None


def _token(context):
    return context.preferences.addons['mmd_tools'].preferences.github_update_token or os.environ.get('MMD_TOOLS_GITHUB_TOKEN', '')


class CheckAddonUpdate(bpy.types.Operator):
    bl_idname = 'mmd_tools.check_addon_update'
    bl_label = '检查续更版更新'
    bl_description = '从晓光忆时的 GitHub 仓库检查正式发行版，不自动安装'
    bl_options = {'INTERNAL'}
    def execute(self, context):
        AddonUpdaterManager.get_instance().start(False, _token(context))
        return {'FINISHED'}


class UpdateAddon(bpy.types.Operator):
    bl_idname = 'mmd_tools.update_addon'
    bl_label = '下载并安装更新'
    bl_description = '校验安装包后更新插件，保留旧版备份；完成后需重启 Blender'
    bl_options = {'INTERNAL'}
    branch_name: bpy.props.StringProperty(default='')
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)
    def execute(self, context):
        updater = AddonUpdaterManager.get_instance()
        if not updater.update_ready():
            self.report({'WARNING'}, '请先检查更新，没有可安装的新版本')
            return {'CANCELLED'}
        updater.start(True, _token(context))
        return {'FINISHED'}


def register_updater(bl_info, init_py_file):
    AddonUpdaterManager.get_instance().init(bl_info, init_py_file)


def unregister_updater():
    updater = AddonUpdaterManager.get_instance()
    updater._active = False
    from .. import preferences
    preferences._session_update_token = ''
    if bpy.app.timers.is_registered(updater.finish): bpy.app.timers.unregister(updater.finish)
