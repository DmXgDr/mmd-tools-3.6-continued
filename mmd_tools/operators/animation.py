# -*- coding: utf-8 -*-

from bpy.types import Operator

from mmd_tools import auto_scene_setup

class SetFrameRange(Operator):
    bl_idname = 'mmd_tools.set_frame_range'
    bl_label = 'Set Frame Range'
    bl_description = '按当前场景动画和 NLA 更新结束帧，保留场景与物理缓存的起始帧；实际帧率为 60 时不变，其余设为 30 fps'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        auto_scene_setup.setupFrameRanges()
        auto_scene_setup.setupFps()
        return {'FINISHED'}
