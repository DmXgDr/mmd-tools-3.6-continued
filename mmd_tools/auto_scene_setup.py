# -*- coding: utf-8 -*-

import math
import bpy

def setupFrameRanges(end_only=False, skip_unused=False):
    s, e = 1, 1
    for i in bpy.data.actions:
        # A fake user keeps an Action saved, but does not play it in the scene.
        if skip_unused and i.users <= int(i.use_fake_user):
            continue
        ts, te = i.frame_range
        s = min(s, ts)
        e = max(e, te)
    if end_only:
        e = max(e, bpy.context.scene.frame_start)
    else:
        bpy.context.scene.frame_start = int(s)
    bpy.context.scene.frame_end = int(e)
    if bpy.context.scene.rigidbody_world is not None:
        if not end_only:
            bpy.context.scene.rigidbody_world.point_cache.frame_start = int(s)
        bpy.context.scene.rigidbody_world.point_cache.frame_end = int(e)

def setupLighting():
    bpy.context.scene.world.light_settings.use_ambient_occlusion = True
    bpy.context.scene.world.light_settings.use_environment_light = True
    bpy.context.scene.world.light_settings.use_indirect_light = True

def setupFps(preserve_60=False):
    render = bpy.context.scene.render
    if preserve_60 and math.isclose(render.fps / render.fps_base, 60.0, rel_tol=1e-6):
        return
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1
