"""Legacy pose-marker storage on Blender 3.6, without replacing animation actions."""
import bpy
import json


def get_library(armature, create=False):
    if armature is None:
        return None
    library = armature.mmd_pose_library or getattr(armature, 'pose_library', None)
    if create:
        if library is None:
            library = bpy.data.actions.new(armature.name + '_PoseLib')
        elif library.library or library.users > 1:
            library = library.copy()
        armature.mmd_pose_library = library
        return library
    if library is None and armature.animation_data:
        action = armature.animation_data.action
        if action and action.pose_markers:
            library = action
    return library


def store_pose(armature, name, bones):
    library = get_library(armature, create=True)
    frame = max((m.frame for m in library.pose_markers), default=0) + 1
    modes = json.loads(library.get('mmd_pose_rotation_modes', '{}'))
    modes[str(frame)] = {b.name: b.rotation_mode for b in bones}
    library['mmd_pose_rotation_modes'] = json.dumps(modes, ensure_ascii=False)
    for bone in bones:
        rotation = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(bone.rotation_mode, 'rotation_euler')
        for prop in ('location', rotation, 'scale'):
            path = bone.path_from_id(prop)
            for index, value in enumerate(getattr(bone, prop)):
                curve = library.fcurves.find(path, index=index) or library.fcurves.new(path, index=index, action_group=bone.name)
                point = curve.keyframe_points.insert(frame, value)
                point.interpolation = 'CONSTANT'
    marker = library.pose_markers.new(name)
    marker.frame = frame
    library.pose_markers.active_index = len(library.pose_markers) - 1
    return library


def apply_pose(armature, library, index):
    """Apply only transforms keyed at this marker; never evaluate unrelated NLA/drivers."""
    if not 0 <= index < len(library.pose_markers):
        raise ValueError('姿态库中没有选中的姿态')
    frame = library.pose_markers[index].frame
    modes = json.loads(library.get('mmd_pose_rotation_modes', '{}')).get(str(frame), {})
    paths = {}
    for bone in armature.pose.bones:
        for prop in ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale'):
            paths[bone.path_from_id(prop)] = (bone, prop)
    for curve in library.fcurves:
        target = paths.get(curve.data_path)
        if target is None or not any(abs(k.co.x - frame) < .001 for k in curve.keyframe_points):
            continue
        bone, prop = target
        mode = modes.get(bone.name)
        if mode in {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}:
            bone.rotation_mode = mode
        if prop == 'rotation_quaternion':
            bone.rotation_mode = 'QUATERNION'
        elif prop == 'rotation_axis_angle':
            bone.rotation_mode = 'AXIS_ANGLE'
        elif prop == 'rotation_euler' and bone.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'}:
            bone.rotation_mode = 'XYZ'
        values = getattr(bone, prop)
        if 0 <= curve.array_index < len(values):
            values[curve.array_index] = curve.evaluate(frame)
