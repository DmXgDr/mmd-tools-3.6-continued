"""Reversible VMD batch import, without rebuilding old NLA strips or drivers."""
from contextvars import ContextVar
import bpy
from ..model import Model

_current = ContextVar('mmd_vmd_transaction', default=None)


def current_transaction():
    return _current.get()


def _values(owner, exclude=()):
    result = {}
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if name in exclude or prop.is_readonly or prop.type == 'COLLECTION':
            continue
        if prop.type == 'POINTER' and not isinstance(getattr(owner, name), (bpy.types.ID, type(None))):
            continue
        value = getattr(owner, name)
        if getattr(prop, 'is_array', False):
            value = value.copy() if hasattr(value, 'copy') else value[:]
        result[name] = value
    return result


def _custom_values(owner):
    return {k: v.to_dict() if hasattr(v, 'to_dict') else v.to_list() if hasattr(v, 'to_list') else v for k, v in owner.items()}


def _restore(owner, values):
    for name, value in values.items():
        setattr(owner, name, value)


def _drivers(owner):
    result = []
    for fc in owner.animation_data.drivers if owner.animation_data else ():
        variables = [(v.type, v.name, [_values(t) for t in v.targets]) for v in fc.driver.variables]
        modifiers = [(m.type, _values(m, ('type',))) for m in fc.modifiers]
        points = [_values(p) for p in fc.keyframe_points]
        result.append((fc.data_path, fc.array_index, _values(fc, ('data_path', 'array_index', 'group', 'driver')), _values(fc.driver), variables, modifiers, points))
    return result


def _restore_drivers(owner, drivers):
    if owner.animation_data:
        for fc in list(owner.animation_data.drivers):
            owner.animation_data.drivers.remove(fc)
    for path, index, props, driver_props, variables, modifiers, points in drivers:
        # Scalar properties reject explicit array indices.
        try:
            fc = owner.driver_add(path, index)
        except TypeError:
            fc = owner.driver_add(path)
        _restore(fc, props)
        for v in list(fc.driver.variables):
            fc.driver.variables.remove(v)
        for kind, name, targets in variables:
            v = fc.driver.variables.new();v.type = kind;v.name = name
            for target, saved in zip(v.targets, targets):
                _restore(target, saved)
        _restore(fc.driver, driver_props)
        for m in list(fc.modifiers):
            fc.modifiers.remove(m)
        for kind, saved in modifiers:
            _restore(fc.modifiers.new(kind), saved)
        fc.keyframe_points.add(len(points))
        for point, saved in zip(fc.keyframe_points, points):
            _restore(point, saved)


class ImportTransaction:
    def __init__(self, context):
        self.context = context
        self.scene = context.scene
        self.frame = (self.scene.frame_current, self.scene.frame_subframe)
        self.timeline = (self.scene.frame_start, self.scene.frame_end, self.scene.render.fps, self.scene.render.fps_base)
        cache = self.scene.rigidbody_world.point_cache if self.scene.rigidbody_world else None
        self.cache = (cache.frame_start, cache.frame_end) if cache else None
        self.selected = list(context.selected_objects)
        self.active = context.view_layer.objects.active
        self.mode = context.object.mode if context.object else 'OBJECT'
        self.original_ids = {name: set(getattr(bpy.data, name)) for name in ('objects', 'actions', 'meshes', 'cameras', 'lights')}
        objects = set(self.selected)
        for obj in self.selected:
            if obj.mmd_type == 'ROOT':
                rig = Model(obj)
                objects.update(rig.meshes())
                # Reading the morph accessor may create a basis shape key.
                placeholder = next((o for o in obj.children if o.mmd_type == 'PLACEHOLDER' and o.type == 'MESH'), None)
                objects.update((rig.armature(), placeholder))
            if obj.type in {'CAMERA', 'LIGHT'}:
                objects.add(obj.parent)
            if obj.mmd_type in {'CAMERA', 'LIGHT'}:
                objects.update(obj.children)
        objects.discard(None)
        self.objects, self.poses, self.keys, self.animations, self.camera_data = {}, {}, {}, {}, {}
        self.mesh_keys = {}
        self.pending_strips, self.retired_actions = [], set()
        self.pending_empty_tracks = []
        self.camera_properties = {o: _values(o.mmd_camera) for o in objects if o.mmd_type == "CAMERA"}
        self.restored = False
        for obj in objects:
            if obj.library and not obj.override_library:
                continue
            attrs = ('parent', 'parent_type', 'parent_bone', 'matrix_parent_inverse', 'rotation_mode', 'location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale', 'lock_location', 'lock_rotation', 'lock_scale', 'hide_viewport', 'hide_render', 'hide_select')
            values = {n: (getattr(obj, n).copy() if not isinstance(getattr(obj, n), bpy.types.ID) and hasattr(getattr(obj, n), 'copy') else getattr(obj, n)[:] if hasattr(getattr(obj, n), '__len__') and not isinstance(getattr(obj, n), (str, bpy.types.ID)) else getattr(obj, n)) for n in attrs}
            self.objects[obj] = (values, obj.data, obj.hide_get(), set(obj.constraints), obj.mmd_root.show_meshes if obj.mmd_type == 'ROOT' else None)
            self.capture_animation(obj, drivers=obj.type == 'CAMERA')
            if obj.type == 'ARMATURE':
                self.poses[obj] = [(bone.name, _values(bone, ('name', 'matrix', 'matrix_basis', 'custom_shape', 'custom_shape_transform', 'bone_group')), _custom_values(bone)) for bone in obj.pose.bones]
            keys = getattr(obj.data, 'shape_keys', None)
            if obj.type == 'MESH':
                self.mesh_keys[obj] = keys
            if keys and keys not in self.keys:
                self.keys[keys] = [(k, k.value, k.slider_min, k.slider_max) for k in keys.key_blocks]
                self.capture_animation(keys)
            if obj.type in {'CAMERA', 'LIGHT'}:
                self.camera_data[obj.data] = (_values(obj.data, ('name', 'animation_data')), _values(obj.data.dof) if obj.type == 'CAMERA' else None)
                self.capture_animation(obj.data, drivers=obj.type == 'CAMERA')

    def capture_animation(self, owner, drivers=False):
        if owner in self.animations:
            return
        ad = owner.animation_data
        tracks = [(track, [(strip, strip.action, strip.mute) for strip in track.strips]) for track in ad.nla_tracks] if ad else []
        self.animations[owner] = (ad.action if ad else None, tracks, ad is not None, _drivers(owner) if drivers else None)

    def __enter__(self):
        self.token = _current.set(self)
        return self

    def defer_strip_removal(self, owner, track, strip):
        strip.mute = True
        if not any(s == strip for _, _, s in self.pending_strips):
            self.pending_strips.append((owner, track, strip))

    def commit(self):
        for owner, track, strip in self.pending_strips:
            track.strips.remove(strip)
        for owner in {owner for owner, _, _ in self.pending_strips} | {owner for owner, _ in self.pending_empty_tracks}:
            for track in list(owner.animation_data.nla_tracks):
                if not track.strips:
                    owner.animation_data.nla_tracks.remove(track)
        from .import_options import remove_unused_replaced_actions
        _current.reset(self.token)
        self.token = None
        remove_unused_replaced_actions(self.retired_actions)

    def rollback(self):
        settings = self.scene.tool_settings
        auto_key = settings.use_keyframe_insert_auto
        settings.use_keyframe_insert_auto = False
        try:
            for obj, (values, data, hidden, constraints, show_meshes) in self.objects.items():
                if obj.data != data:
                    obj.data = data
                if obj in self.mesh_keys and self.mesh_keys[obj] is None and obj.data.shape_keys:
                    obj.shape_key_clear()
                for constraint in list(obj.constraints):
                    if constraint not in constraints:
                        obj.constraints.remove(constraint)
                _restore(obj, values)
                obj.hide_set(hidden)
                if show_meshes is not None:
                    obj.mmd_root.show_meshes = show_meshes
            for obj, values in self.camera_properties.items():
                _restore(obj.mmd_camera, values)
            for data, (values, dof) in self.camera_data.items():
                _restore(data, values)
                if dof is not None:
                    _restore(data.dof, dof)
            for owner, (action, tracks, had_animation, drivers) in self.animations.items():
                ad = owner.animation_data
                if ad:
                    old_tracks = {t for t, _ in tracks}
                    for track in list(ad.nla_tracks):
                        if track not in old_tracks:
                            ad.nla_tracks.remove(track)
                    ad.action = action
                    for track, strips in tracks:
                        for strip, old_action, mute in strips:
                            strip.action, strip.mute = old_action, mute
                if drivers is not None and _drivers(owner) != drivers:
                    _restore_drivers(owner, drivers)
                if not had_animation and owner.animation_data:
                    owner.animation_data_clear()
            # Remove only newly allocated, now unreferenced import data.
            for obj in set(bpy.data.objects) - self.original_ids['objects']:
                bpy.data.objects.remove(obj, do_unlink=True)
            for name in ('actions', 'meshes', 'cameras', 'lights'):
                collection = getattr(bpy.data, name)
                for data in set(collection) - self.original_ids[name]:
                    if data.users == 0:
                        collection.remove(data)
            start, end, fps, base = self.timeline
            self.scene.frame_start, self.scene.frame_end = start, end
            self.scene.render.fps, self.scene.render.fps_base = fps, base
            if self.cache and self.scene.rigidbody_world:
                cache = self.scene.rigidbody_world.point_cache
                cache.frame_start, cache.frame_end = self.cache
            self.scene.frame_set(self.frame[0], subframe=self.frame[1])
            for obj, poses in self.poses.items():
                for name, values, custom in poses:
                    bone = obj.pose.bones[name]
                    _restore(bone, values)
                    for key in list(bone.keys()):
                        if key not in custom:
                            del bone[key]
                    for key, value in custom.items():
                        bone[key] = value
            for values in self.keys.values():
                for key, value, minimum, maximum in values:
                    key.slider_min, key.slider_max, key.value = minimum, maximum, value
            for obj in self.context.selected_objects:
                obj.select_set(False)
            for obj in self.selected:
                obj.select_set(True)
            self.context.view_layer.objects.active = self.active
            self.restored = True
        finally:
            settings.use_keyframe_insert_auto = auto_key

    def __exit__(self, kind, error, tb):
        try:
            if self.token is not None:
                self.rollback()
        finally:
            if self.token is not None:
                _current.reset(self.token)
                self.token = None
