# -*- coding: utf-8 -*-

import math
import bpy

def _scene_animation_owners(scene):
    """Only IDs reachable from this scene; orphan Actions never enter the scan."""
    seen, collections = set(), set()
    pending = [scene, scene.world] + list(scene.objects)
    while pending:
        owner = pending.pop()
        if owner is None or owner in seen:
            continue
        seen.add(owner)
        yield owner
        for name in ('data', 'shape_keys', 'node_tree'):
            related = getattr(owner, name, None)
            if isinstance(related, bpy.types.ID):
                pending.append(related)
        if isinstance(owner, bpy.types.NodeTree):
            pending.extend(node.node_tree for node in owner.nodes if node.type == 'GROUP' and node.node_tree)
        if isinstance(owner, bpy.types.Object):
            pending.extend(slot.material for slot in owner.material_slots if slot.material)
            collection = owner.instance_collection
            if owner.instance_type == 'COLLECTION' and collection and collection not in collections:
                collections.add(collection)
                pending.extend(collection.all_objects)


def _animation_ends(owner):
    animation = getattr(owner, 'animation_data', None)
    if not animation:
        return
    tracks = list(animation.nla_tracks) if animation.use_nla else []
    solo = any(t.is_solo for t in tracks)
    if animation.action and not solo and animation.action.fcurves:
        yield animation.action.curve_frame_range[1]
    for track in tracks:
        if track.mute or (solo and not track.is_solo):
            continue
        for strip in track.strips:
            if not strip.mute:
                # Includes offsets, repeat, scale, animated time and meta bounds.
                yield strip.frame_end


def setupFrameRanges(end_only=True, skip_unused=True):
    """Compatibility arguments retained; both UI entry points update only end."""
    scene = bpy.context.scene
    end = max((frame for owner in _scene_animation_owners(scene) for frame in _animation_ends(owner)), default=scene.frame_start)
    scene.frame_end = max(scene.frame_start, math.ceil(end))
    if scene.rigidbody_world:
        cache = scene.rigidbody_world.point_cache
        cache.frame_end = max(cache.frame_start, scene.frame_end)

def setupLighting():
    bpy.context.scene.world.light_settings.use_ambient_occlusion = True
    bpy.context.scene.world.light_settings.use_environment_light = True
    bpy.context.scene.world.light_settings.use_indirect_light = True

def setupFps(preserve_60=True):
    render = bpy.context.scene.render
    if math.isclose(render.fps / render.fps_base, 60.0, rel_tol=1e-6):
        return
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1
