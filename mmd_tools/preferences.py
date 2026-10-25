# -*- coding: utf-8 -*-

import os

import bpy

from mmd_tools import operators
from . import preset_store

_session_update_token = ''


def _get_update_token(self):
    return _session_update_token


def _set_update_token(self, value):
    global _session_update_token
    _session_update_token = value


def _get_update_candidate_branches(_, __):
    updater = operators.addon_updater.AddonUpdaterManager.get_instance()
    if not updater.candidate_checked():
        return []

    return [(name, name, "") for name in updater.get_candidate_branch_names()]


def get_additional_unallowed_chars(prop_name):
    addon = bpy.context.preferences.addons.get(__package__)
    return getattr(addon.preferences, 'additional_unallowed_chars_bone', '') if addon and prop_name == 'bone' else ''

def get_replacement_char():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences.replacement_char if addon else '_'

_preset_enum_cache = {}


def _update_drag_drop(self, context):
    from . import drag_drop
    if not self.enable_mmd_drag_drop:
        drag_drop.shutdown()
        drag_drop._incoming.clear()
        drag_drop._jobs.clear()


def get_preset_items_for_operator(operator_bl_idname):
    """Get preset items for EnumProperty"""
    items = [("__DEFAULT__", "Default", "Use built-in defaults")]
    try:
        from .operators import fileio

        presets = fileio.get_available_presets(operator_bl_idname)
        saved = preset_store.records().get(operator_bl_idname, {}).get('name')
        if saved and saved != '__DEFAULT__' and saved not in presets:
            presets.append(saved)
        items.extend((preset, preset, f"Use preset: {preset}") for preset in presets)
    except Exception:
        pass
    # Retain every returned string across redraws, even if preset files change.
    return _preset_enum_cache.setdefault((operator_bl_idname, tuple(items)), items)

def get_pmx_import_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.import_model")

def get_vmd_import_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.import_vmd")

def get_vpd_import_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.import_vpd")

def get_pmx_export_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.export_pmx")

def get_vmd_export_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.export_vmd")

def get_vpd_export_preset_items(self, context):
    return get_preset_items_for_operator("mmd_tools.export_vpd")


class MMDToolsAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_update_auth: bpy.props.BoolProperty(name='私密仓库验证（可选）', default=False, options={'SKIP_SAVE'})
    github_update_token: bpy.props.StringProperty(name='GitHub 令牌', subtype='PASSWORD', options={'SKIP_SAVE'}, get=_get_update_token, set=_set_update_token,
        description='仅本次会话使用，不写入偏好设置或工程；私密测试需仓库 Contents 读取权限，公开后无需填写')

    enable_mmd_drag_drop: bpy.props.BoolProperty(
        name='启用 MMD 拖放导入', default=True, update=_update_drag_drop,
        description='接收 Windows 资源管理器拖入的 PMX、PMD、VMD、VPD；关闭时保留文件菜单导入和已打开的导入窗口')

    enable_mmd_model_production_features: bpy.props.BoolProperty(
        name="Enable MMD Model Production Features",
        default=True,
    )
    shared_toon_folder: bpy.props.StringProperty(
        name="Shared Toon Texture Folder",
        description=('Directory path to toon textures. This is normally the "Data" directory within of your MikuMikuDance directory'),
        subtype='DIR_PATH',
        default=os.path.join(os.path.dirname(__file__), 'externals', 'MikuMikuDance'),
    )
    base_texture_folder: bpy.props.StringProperty(
        name='Base Texture Folder',
        description='Path for textures shared between models',
        subtype='DIR_PATH',
    )
    dictionary_folder: bpy.props.StringProperty(
        name='Dictionary Folder',
        description='Path for searching csv dictionaries',
        subtype='DIR_PATH',
        default=os.path.dirname(__file__),
    )

    additional_unallowed_chars_bone: bpy.props.StringProperty(
        name='骨骼名称额外禁用字符', default='',
        description='逐个字符检查；修复时替换为下方字符')
    replacement_char: bpy.props.StringProperty(
        name='替换为', default='_', maxlen=1,
        description='留空则删除额外禁用字符')

    default_pmx_import_preset: bpy.props.EnumProperty(
        name="Default PMX Import Operator Preset",
        description="Default preset to use for PMX import operations",
        items=get_pmx_import_preset_items,
        get=preset_store.callbacks('mmd_tools.import_model', 'default_pmx_import_preset')[0],
        set=preset_store.callbacks('mmd_tools.import_model', 'default_pmx_import_preset')[1],
        default=0,
    )

    default_vmd_import_preset: bpy.props.EnumProperty(
        name="Default VMD Import Operator Preset",
        description="Default preset to use for VMD import operations",
        items=get_vmd_import_preset_items,
        get=preset_store.callbacks('mmd_tools.import_vmd', 'default_vmd_import_preset')[0],
        set=preset_store.callbacks('mmd_tools.import_vmd', 'default_vmd_import_preset')[1],
        default=0,
    )

    default_vpd_import_preset: bpy.props.EnumProperty(
        name="Default VPD Import Operator Preset",
        description="Default preset to use for VPD import operations",
        items=get_vpd_import_preset_items,
        get=preset_store.callbacks('mmd_tools.import_vpd', 'default_vpd_import_preset')[0],
        set=preset_store.callbacks('mmd_tools.import_vpd', 'default_vpd_import_preset')[1],
        default=0,
    )

    default_pmx_export_preset: bpy.props.EnumProperty(
        name="Default PMX Export Operator Preset",
        description="Default preset to use for PMX export operations",
        items=get_pmx_export_preset_items,
        get=preset_store.callbacks('mmd_tools.export_pmx', 'default_pmx_export_preset')[0],
        set=preset_store.callbacks('mmd_tools.export_pmx', 'default_pmx_export_preset')[1],
        default=0,
    )

    default_vmd_export_preset: bpy.props.EnumProperty(
        name="Default VMD Export Operator Preset",
        description="Default preset to use for VMD export operations",
        items=get_vmd_export_preset_items,
        get=preset_store.callbacks('mmd_tools.export_vmd', 'default_vmd_export_preset')[0],
        set=preset_store.callbacks('mmd_tools.export_vmd', 'default_vmd_export_preset')[1],
        default=0,
    )

    default_vpd_export_preset: bpy.props.EnumProperty(
        name="Default VPD Export Operator Preset",
        description="Default preset to use for VPD export operations",
        items=get_vpd_export_preset_items,
        get=preset_store.callbacks('mmd_tools.export_vpd', 'default_vpd_export_preset')[0],
        set=preset_store.callbacks('mmd_tools.export_vpd', 'default_vpd_export_preset')[1],
        default=0,
    )

    # for add-on updater
    updater_branch_to_update: bpy.props.EnumProperty(
        name='Branch',
        description='Target branch to update add-on',
        items=_get_update_candidate_branches
    )

    def draw(self, _context):
        layout: bpy.types.UILayout = self.layout # pylint: disable=no-member
        layout.prop(self, "enable_mmd_model_production_features")
        layout.prop(self, 'enable_mmd_drag_drop')
        layout.prop(self, "shared_toon_folder")
        layout.prop(self, "base_texture_folder")
        layout.prop(self, "dictionary_folder")


        layout.separator()
        layout.label(text='默认操作项预设：')
        layout.prop(self, "default_pmx_import_preset", text="PMX 导入")
        layout.prop(self, "default_pmx_export_preset", text="PMX 导出")
        layout.prop(self, "default_vmd_import_preset", text="VMD 导入")
        layout.prop(self, "default_vmd_export_preset", text="VMD 导出")
        layout.prop(self, "default_vpd_import_preset", text="VPD 导入")
        layout.prop(self, "default_vpd_export_preset", text="VPD 导出")

        box = layout.box()
        box.label(text='模型调试：骨骼名称')
        box.prop(self, 'additional_unallowed_chars_bone')
        box.prop(self, 'replacement_char')

        from .update_service import REPOSITORY_URL
        update_col = layout.box().column(align=False)
        update_col.label(text='续更版在线更新', icon='RECOVER_LAST')
        update_col.operator('wm.url_open', text='晓光忆时的 MMD Tools GitHub', icon='URL').url = REPOSITORY_URL
        updater = operators.addon_updater.AddonUpdaterManager.get_instance()
        row = update_col.row(align=True)
        row.enabled = not updater.busy and not updater.updated()
        row.operator('mmd_tools.check_addon_update', icon='FILE_REFRESH')
        install = row.row(align=True)
        install.enabled = updater.update_ready()
        install.operator('mmd_tools.update_addon', icon='IMPORT').branch_name = updater.latest_version()
        if updater.message:
            update_col.label(text=updater.message, icon='INFO')
        if updater.failure:
            update_col.label(text=updater.failure, icon='ERROR')
        if updater.updated():
            update_col.label(text='请保存工程后手动重启；旧插件已备份。', icon='ERROR')
        update_col.prop(self, 'show_update_auth')
        if self.show_update_auth:
            update_col.prop(self, 'github_update_token')
            update_col.label(text='仅本次会话有效；公开仓库不需要令牌。')
