# Copyright 2025 MMD Tools authors. GPL-3.0-or-later.
# Bone-only layout adapted from v4.5.14 panels/sidebar/model_debug.py.
import bpy
from ..core.model import Model

class MMDModelDebugPanel(bpy.types.Panel):
    bl_idname = 'OBJECT_PT_mmd_tools_model_debug'
    bl_label = '模型调试'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MMD'
    bl_order = 4

    @classmethod
    def poll(cls, context):
        addon = context.preferences.addons.get('mmd_tools')
        return getattr(addon.preferences, 'enable_mmd_model_production_features', True) if addon else True

    def draw(self, context):
        root = Model.findRoot(context.active_object)
        layout = self.layout
        col = layout.column(align=True)
        col.label(text='骨骼检查：', icon='ERROR')
        col.enabled = root is not None
        col.operator('mmd_tools.validate_bones', text='检查骨骼', icon='BONE_DATA')
        box = layout.box()
        box.label(text='检查结果：', icon='INFO')
        results = context.scene.mmd_validation_results
        if results:
            lines = results.split('\n')
            for line in lines[:10]:
                box.label(text=line)
            if len(lines) > 10:
                box.label(text='另有 %d 条结果' % (len(lines)-10), icon='LINENUMBERS_ON')
                box.label(text='完整日志请查看系统控制台', icon='CONSOLE')
        else:
            box.label(text='点击检查骨骼以显示结果')
        col = layout.column(align=True)
        col.label(text='快速修复：', icon='TOOL_SETTINGS')
        col.enabled = root is not None
        col.operator('mmd_tools.fix_bone_issues', text='修复骨骼', icon='MODIFIER')
