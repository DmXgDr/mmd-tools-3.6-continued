"""Blender 3.6 import-only timing and selected-model reset helpers."""
import math
import bpy
from mathutils import Matrix, Euler
from ..model import Model


def selected_model_roots(context):
    return [obj for obj in context.selected_objects
            if getattr(obj, 'mmd_type', None) == 'ROOT' and Model.findRoot(obj) == obj]


def _camera_delta(a, b):
    position = math.sqrt(sum((x-y)**2 for x, y in zip(a.location, b.location)))
    qa, qb = Euler(a.rotation, 'YXZ').to_quaternion(), Euler(b.rotation, 'YXZ').to_quaternion()
    angle = qa.rotation_difference(qb).angle
    angle = math.degrees(min(angle, 2*math.pi-angle))
    return position, angle, abs(a.distance-b.distance), abs(a.angle-b.angle)


def camera_cut_frames(frames):
    """Sparse camera: one-frame pairs; baked camera: clear jumps only.

    VMD has no baked/cut flag. A run of >=8 consecutive keys is dense, or the
    entire file is dense when >=80% of gaps are one frame (minimum 8 keys,
    no gap greater than 2). Longer gaps split sparse and baked sections.
    Dense data requires both an absolute jump and >=5x neighboring speed;
    perspective switches are discrete. Source units make this scale independent.
    """
    frames = sorted(frames, key=lambda key: key.frame_number)
    gaps = [b.frame_number-a.frame_number for a, b in zip(frames, frames[1:])]
    deltas = [_camera_delta(a, b) for a, b in zip(frames, frames[1:])]
    dense = set()
    if (len(frames) >= 8 and max(gaps) <= 2
            and sum(gap == 1 for gap in gaps) >= .8*len(gaps)):
        dense.update(range(len(gaps)))
    start = 0
    for end in range(len(gaps)+1):
        if end == len(gaps) or gaps[end] != 1:
            if end-start >= 7:
                dense.update(range(start, end))
            start = end+1
    result = set()
    for i, (a, b) in enumerate(zip(frames, frames[1:])):
        if gaps[i] != 1:
            continue
        if i not in dense or a.persp != b.persp:
            result.add(b.frame_number)
            continue
        for channel, threshold in enumerate((10.0, 15.0, 10.0, 10.0)):
            speed = max((deltas[j][channel]/gaps[j] for j in (i-1, i+1)
                         if 0 <= j < len(gaps) and gaps[j] > 0), default=0.0)
            if deltas[i][channel] >= max(threshold, 5.0*speed):
                result.add(b.frame_number)
                break
    return result


def retime_action_60fps(action, cut_frames=()):
    """Scale a newly imported action about frame zero, then close cut gaps.

    Handle coordinates follow exactly the same affine transform as each key.
    Existing/shared actions and drivers are never retimed here.
    """
    cuts = set(cut_frames)
    for curve in action.fcurves:
        points = list(curve.keyframe_points)
        original = [float(key.co.x) for key in points]
        for key, frame in zip(points, original):
            shift = -1.0 if frame in cuts else 0.0
            co, left, right = key.co.copy(), key.handle_left.copy(), key.handle_right.copy()
            co.x = co.x*2.0 + shift
            left.x = left.x*2.0 + shift
            right.x = right.x*2.0 + shift
            key.co = co
            key.handle_left = left
            key.handle_right = right
        curve.update()


def _actions(owner):
    animation = owner.animation_data
    if not animation:
        return []
    result = [animation.action] if animation.action else []
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action:
                result.append(strip.action)
    return result


def _remove_channels(owner, predicate):
    """Detach just the requested channels, copying actions to protect other users."""
    animation = owner.animation_data
    if not animation:
        return
    copies = {}
    def without(action):
        if not action or not any(predicate(fc) for fc in action.fcurves):
            return action
        if action.as_pointer() not in copies:
            if all(predicate(fc) for fc in action.fcurves):
                copies[action.as_pointer()] = None
            else:
                copy = action.copy()
                for fc in list(copy.fcurves):
                    if predicate(fc):
                        copy.fcurves.remove(fc)
                copies[action.as_pointer()] = copy
        return copies[action.as_pointer()]
    animation.action = without(animation.action)
    for track in list(animation.nla_tracks):
        for strip in list(track.strips):
            cleaned = without(strip.action)
            if strip.action and cleaned is None:
                track.strips.remove(strip)
            elif cleaned != strip.action:
                strip.action = cleaned
        if not track.strips:
            animation.nla_tracks.remove(track)


def _reset_pose(context, armature):
    """Reset existing pose without generating new keys as a side effect.

    Blender 3.6's native clear-transform operator also runs the automatic
    keying-set callbacks when Auto Keying is enabled. Besides recreating the
    animation being cleared, that path can crash with Python worker threads
    active in other add-ons. Keep both native reset operations, but suspend
    Auto Keying for this internal reset and always restore the user's setting.
    """
    if armature is None:
        return
    settings = context.scene.tool_settings
    previous_auto_key = settings.use_keyframe_insert_auto
    try:
        settings.use_keyframe_insert_auto = False
        return _reset_pose_without_autokey(context, armature)
    finally:
        settings.use_keyframe_insert_auto = previous_auto_key


def _reset_pose_without_autokey(context, armature):
    if armature is None:
        return
    _remove_channels(armature, lambda fc: fc.data_path.startswith('pose.bones['))
    mode = armature.mode
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    object_hidden = (armature.hide_viewport, armature.hide_get(), armature.hide_select)
    states = [(bone, bone.select, bone.hide, bone.hide_select) for bone in armature.data.bones]
    layers = tuple(armature.data.layers)
    try:
        for obj in selected:
            obj.select_set(False)
        armature.hide_viewport = armature.hide_select = False
        armature.hide_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        for bone, *_ in states:
            bone.select = True
            bone.hide = bone.hide_select = False
        armature.data.layers = [True]*32
        with context.temp_override(object=armature, active_object=armature, pose_object=armature,
                                   selected_objects=[armature], selected_editable_objects=[armature]):
            if armature.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')
            # Native menu operations requested by the user, including reset of
            # unkeyed pose properties. The identity pass includes locked bones.
            bpy.ops.pose.transforms_clear()
            bpy.ops.pose.user_transforms_clear(only_selected=False)
            for bone in armature.pose.bones:
                bone.matrix_basis = Matrix.Identity(4)
            if armature.mode != mode:
                bpy.ops.object.mode_set(mode=mode)
    finally:
        if armature.mode != mode:
            with context.temp_override(object=armature, active_object=armature):
                bpy.ops.object.mode_set(mode=mode)
        armature.data.layers = layers
        for bone, bone_selected, hidden, hide_select in states:
            bone.select, bone.hide, bone.hide_select = bone_selected, hidden, hide_select
        armature.select_set(False)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        armature.hide_viewport, hidden, armature.hide_select = object_hidden
        armature.hide_set(hidden)


def clear_model_motion(context, root):
    rig = Model(root)
    meshes = list(rig.meshes())
    placeholder = rig.morph_slider.placeholder()
    if placeholder is not None:
        meshes.append(placeholder)
    # A linked mesh may also be used by an unselected character. Isolate the
    # selected model's mesh/Key datablock before touching its animation or values.
    members = set(meshes)
    copies = {}
    for mesh in meshes:
        data = mesh.data
        if data.users > 1 and any(obj.type == 'MESH' and obj.data == data and obj not in members
                                 for obj in bpy.data.objects):
            if data.as_pointer() not in copies:
                copies[data.as_pointer()] = data.copy()
            mesh.data = copies[data.as_pointer()]
    seen = set()
    # Drivers are deliberately excluded: the morph binding remains intact.
    for mesh in meshes:
        keys = mesh.data.shape_keys
        if keys is None or keys.as_pointer() in seen:
            continue
        seen.add(keys.as_pointer())
        paths = {fc.data_path for action in _actions(keys) for fc in action.fcurves
                 if len(fc.keyframe_points) or len(fc.sampled_points)}
        keyed = [key for key in keys.key_blocks if key.path_from_id('value') in paths]
        keyed_paths = {key.path_from_id('value') for key in keyed}
        _remove_channels(keys, lambda fc: fc.data_path in keyed_paths)
        for key in keyed:
            key.value = 0.0
    _reset_pose(context, rig.armature())
    context.view_layer.update()
