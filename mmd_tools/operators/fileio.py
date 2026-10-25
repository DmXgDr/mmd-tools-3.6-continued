# -*- coding: utf-8 -*-

import ast
import logging
import re
import traceback
import os
import time
import math

import bpy
from bpy.types import Operator
from bpy.types import OperatorFileListElement
from bpy_extras.io_utils import ImportHelper, ExportHelper

from mmd_tools import auto_scene_setup
from mmd_tools.utils import makePmxBoneMap
from mmd_tools.core.camera import MMDCamera
from mmd_tools.core.lamp import MMDLamp
from mmd_tools.translations import DictionaryEnum
from mmd_tools.audio_transfer import motion_audio_items

import mmd_tools.core.pmd.importer as pmd_importer
import mmd_tools.core.pmx.importer as pmx_importer
import mmd_tools.core.pmx.exporter as pmx_exporter
import mmd_tools.core.vmd.importer as vmd_importer
import mmd_tools.core.vmd.exporter as vmd_exporter
import mmd_tools.core.vpd.importer as vpd_importer
import mmd_tools.core.vpd.exporter as vpd_exporter
import mmd_tools.core.model as mmd_model



LOG_LEVEL_ITEMS = [
    ('DEBUG', '4. DEBUG', '', 1),
    ('INFO', '3. INFO', '', 2),
    ('WARNING', '2. WARNING', '', 3),
    ('ERROR', '1. ERROR', '', 4),
    ]

def log_handler(log_level, filepath=None):
    if filepath is None:
        handler = logging.StreamHandler()
    else:
        handler = logging.FileHandler(filepath, mode='w', encoding='utf-8')
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    return handler


def _update_types(cls, prop):
    types = cls.types.copy()

    if 'PHYSICS' in types:
        types.add('ARMATURE')
    if 'DISPLAY' in types:
        types.add('ARMATURE')
    if 'MORPHS' in types:
        types.add('ARMATURE')
        types.add('MESH')

    if types != cls.types:
        cls.types = types # trigger update

def get_addon_package_name():
    """Get the root package name for addon preferences"""
    current_package = __package__
    parts = current_package.split(".")
    try:
        index = parts.index("mmd_tools")
        return ".".join(parts[: index + 1])
    except ValueError:
        pass
    return current_package

def get_preset_directories(operator_bl_idname):
    """Get preset directories for an operator"""
    if operator_bl_idname.startswith("mmd_tools_drop."):
        operator_bl_idname = operator_bl_idname.replace("mmd_tools_drop.", "mmd_tools.", 1)
    preset_dirs = []

    try:
        # Try the official API first
        official_dirs = bpy.utils.preset_paths(operator_bl_idname)
        preset_dirs.extend(official_dirs)

        preset_dirs.extend(bpy.utils.preset_paths("operator/" + operator_bl_idname))
        # Add manual preset paths as fallback
        scripts_dir = bpy.utils.user_resource("SCRIPTS")
        config_dir = bpy.utils.user_resource("CONFIG")

        manual_preset_paths = [
            os.path.join(scripts_dir, "presets", "operator", operator_bl_idname),
            os.path.join(config_dir, "presets", "operator", operator_bl_idname),
        ]

        if operator_bl_idname in {"mmd_tools.import_model", "mmd_tools.import_vmd", "mmd_tools.import_vpd"}:
            alias = operator_bl_idname.replace("mmd_tools.", "mmd_tools_drop.", 1)
            manual_preset_paths.extend(bpy.utils.preset_paths("operator/" + alias))
        for path in manual_preset_paths:
            if os.path.exists(path) and path not in preset_dirs:
                preset_dirs.append(path)

    except Exception:
        pass

    return preset_dirs

def _parse_preset_literal(node):
    """Parse a literal value node from an operator preset file's AST."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_parse_preset_literal(element) for element in node.elts)
    if isinstance(node, ast.List):
        return [_parse_preset_literal(element) for element in node.elts]
    if isinstance(node, ast.Set):
        return {_parse_preset_literal(element) for element in node.elts}
    if isinstance(node, ast.Dict):
        return {_parse_preset_literal(key): _parse_preset_literal(value) for key, value in zip(node.keys, node.values, strict=False)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _parse_preset_literal(node.operand)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    raise ValueError(f"Unsupported preset value: {type(node).__name__}")

def apply_operator_preset(operator, preset_name):
    """Apply a saved preset to an operator instance"""
    if not preset_name:
        return False

    try:
        from .. import preset_store
        operator_id = operator.__class__.bl_idname.replace('mmd_tools_drop.', 'mmd_tools.', 1)
        preset_code = preset_store.source(operator_id, preset_name)
        if preset_code is None:
            return False

        # Apply the preset by parsing its assignments instead of executing it.
        # Blender operator preset files only contain literal assignments of the
        # form `op.<property> = <literal>`, so they can be applied safely without
        # exec by reading those assignments from the AST.
        try:
            tree = ast.parse(preset_code, filename=preset_name + '.py', mode="exec")
            applied = 0
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                targets = [t for t in node.targets if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == 'op']
                if not targets:
                    continue
                try:
                    value = _parse_preset_literal(node.value)
                except ValueError:
                    continue
                for target in targets:
                    prop = operator.properties.bl_rna.properties.get(target.attr)
                    if prop is None or prop.is_readonly or prop.type in {'POINTER', 'COLLECTION'}:
                        continue
                    if target.attr in {'filepath', 'directory', 'filter_glob'}:
                        continue
                    try:
                        setattr(operator, target.attr, value)
                        applied += 1
                    except (TypeError, ValueError):
                        # Presets may contain retired options/dictionary entries.
                        # One incompatible field must not suppress later fields.
                        continue
            if applied:
                preset_store.save(operator_id, preset_name, preset_code)
            return bool(applied)

        except Exception:
            return False

    except Exception:
        return False

def get_available_presets(operator_bl_idname):
    """Get list of available presets for an operator"""
    presets = []

    try:
        preset_dirs = get_preset_directories(operator_bl_idname)

        for preset_dir in preset_dirs:
            try:
                for filename in os.listdir(preset_dir):
                    if filename.endswith(".py"):
                        preset_name = filename[:-3]  # Remove .py extension
                        if preset_name not in presets:
                            presets.append(preset_name)
            except Exception:
                continue

        return sorted(presets)

    except Exception:
        return []

def load_default_settings_from_preferences(operator, context, preset_property_name):
    """Load default settings from preferences using preset"""
    try:
        addon_package = get_addon_package_name()
        addon_prefs = context.preferences.addons.get(addon_package)

        if not addon_prefs:
            return False

        prefs = addon_prefs.preferences

        # Check if the preset property exists
        if not hasattr(prefs, preset_property_name):
            return False

        # Apply preset if specified
        preset_name = getattr(prefs, preset_property_name, "")
        if preset_name and apply_operator_preset(operator, preset_name):
            return True

        return False

    except Exception:
        return False

def initialize_vmd_timing(operator, context, explicit=None):
    """Refresh the scene-dependent default on every INVOKE, not from history."""
    from .. import preset_store
    chosen = None
    addon = context.preferences.addons.get(get_addon_package_name())
    if addon:
        name = addon.preferences.default_vmd_import_preset
        code = preset_store.source('mmd_tools.import_vmd', name) if name != '__DEFAULT__' else None
        if code:
            try:
                for node in ast.parse(code).body:
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name) and t.value.id == 'op'
                            and t.attr == 'convert_to_60fps' for t in node.targets):
                        value = _parse_preset_literal(node.value)
                        if isinstance(value, bool):chosen = value
            except (SyntaxError, ValueError):
                pass
    fps = context.scene.render.fps / context.scene.render.fps_base
    operator.convert_to_60fps = (explicit if explicit is not None else chosen
                                if chosen is not None else math.isclose(fps, 60.0, rel_tol=1e-6))


class PreferencesMixin:
    """Mixin for operators that load default settings from preferences"""

    _preferences_signature = None

    def load_preferences_on_invoke(self, context, preset_property_name):
        """Session edits survive; a changed default preset takes effect immediately."""
        from .. import preset_store
        addon = context.preferences.addons.get(get_addon_package_name())
        name = getattr(addon.preferences, preset_property_name, '__DEFAULT__') if addon else '__DEFAULT__'
        operator_id = self.__class__.bl_idname.replace('mmd_tools_drop.', 'mmd_tools.', 1)
        code = preset_store.source(operator_id, name) if name != '__DEFAULT__' else None
        signature = (name, code)
        previous = self.__class__.__dict__.get('_preferences_signature')
        self._previous_preferences_signature = previous
        if signature != previous:
            if name == '__DEFAULT__':
                if previous is not None:
                    for prop in self.properties.bl_rna.properties:
                        if prop.identifier != 'rna_type' and not prop.is_readonly and not prop.is_hidden and not prop.is_skip_save and prop.type not in {'COLLECTION', 'POINTER'}:
                            self.properties.property_unset(prop.identifier)
                self.__class__._preferences_signature = signature
            elif load_default_settings_from_preferences(self, context, preset_property_name):
                self.__class__._preferences_signature = signature

    def restore_preferences_on_cancel(self):
        """Restore preferences state on cancel"""
        self.__class__._preferences_signature = getattr(self, '_previous_preferences_signature', None)


class ImportPmx(Operator, ImportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.import_model'
    bl_label = 'Import Model File (.pmd, .pmx)'
    bl_description = 'Import model file(s) (.pmd, .pmx)'
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    files: bpy.props.CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: bpy.props.StringProperty(maxlen=1024, subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    filename_ext = '.pmx'
    filter_glob: bpy.props.StringProperty(default='*.pmx;*.pmd', options={'HIDDEN'})

    types: bpy.props.EnumProperty(
        name='Types',
        description='Select which parts will be imported',
        options={'ENUM_FLAG'},
        items = [
            ('MESH', 'Mesh', 'Mesh', 1),
            ('ARMATURE', 'Armature', 'Armature', 2),
            ('PHYSICS', 'Physics', 'Rigidbodies and joints (include Armature)', 4),
            ('DISPLAY', 'Display', 'Display frames (include Armature)', 8),
            ('MORPHS', 'Morphs', 'Morphs (include Armature and Mesh)', 16),
            ],
        default={'MESH', 'ARMATURE', 'PHYSICS', 'DISPLAY', 'MORPHS',},
        update=_update_types,
        )
    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for importing the model',
        default=0.08,
        )
    clean_model: bpy.props.BoolProperty(
        name='Clean Model',
        description='Remove unused vertices and duplicated/invalid faces',
        default=True,
        )
    fast_physics: bpy.props.BoolProperty(
        name='加速物理对象导入',
        description='减少刚体和关节复制时的场景更新；关闭可使用原版复制流程',
        default=True,
        )
    remove_doubles: bpy.props.BoolProperty(
        name='Remove Doubles',
        description='Merge duplicated vertices and faces',
        default=False,
        )
    fix_IK_links: bpy.props.BoolProperty(
        name='Fix IK Links',
        description='Fix IK links to be blender suitable',
        default=False,
        )
    ik_loop_factor: bpy.props.IntProperty(
        name='IK Loop Factor',
        description='Scaling factor of MMD IK loop',
        min=1,
        soft_max=10, max=100,
        default=5,
        )
    apply_bone_fixed_axis: bpy.props.BoolProperty(
        name='Apply Bone Fixed Axis',
        description="Apply bone's fixed axis to be blender suitable",
        default=False,
        )
    rename_bones: bpy.props.BoolProperty(
        name='Rename Bones - L / R Suffix',
        description='Use Blender naming conventions for Left / Right paired bones',
        default=True,
        )
    use_underscore: bpy.props.BoolProperty(
        name="Rename Bones - Use Underscore",
        description='Will not use dot, e.g. if renaming bones, will use _R instead of .R',
        default=False,
        )
    dictionary: bpy.props.EnumProperty(
        name='Rename Bones To English',
        items=DictionaryEnum.get_dictionary_items,
        description='Translate bone names from Japanese to English using selected dictionary',
        )
    use_mipmap: bpy.props.BoolProperty(
        name='use MIP maps for UV textures',
        description='Specify if mipmaps will be generated',
        default=True,
        )
    sph_blend_factor: bpy.props.FloatProperty(
        name='influence of .sph textures',
        description='The diffuse color factor of texture slot for .sph textures',
        default=1.0,
        )
    spa_blend_factor: bpy.props.FloatProperty(
        name='influence of .spa textures',
        description='The diffuse color factor of texture slot for .spa textures',
        default=1.0,
        )
    log_level: bpy.props.EnumProperty(
        name='Log level',
        description='Select log level',
        items=LOG_LEVEL_ITEMS,
        default='INFO',
        )
    save_log: bpy.props.BoolProperty(
        name='Create a log file',
        description='Create a log file',
        default=False,
        )

    def execute(self, context):
        try:
            self.__translator = DictionaryEnum.get_translator(self.dictionary)
            if self.directory:
                for f in self.files:
                    self.filepath = os.path.join(self.directory, f.name)
                    self._do_execute(context)
            elif self.filepath:
                self._do_execute(context)
        except Exception as e:
            err_msg = traceback.format_exc()
            self.report({'ERROR'}, err_msg)
        return {'FINISHED'}

    def _do_execute(self, context):
        logger = logging.getLogger()
        logger.setLevel(self.log_level)
        if self.save_log:
            handler = log_handler(self.log_level, filepath=self.filepath + '.mmd_tools.import.log')
            logger.addHandler(handler)
        try:
            importer_cls = pmx_importer.PMXImporter
            if re.search('\.pmd$', self.filepath, flags=re.I):
                importer_cls = pmd_importer.PMDImporter

            importer_cls().execute(
                filepath=self.filepath,
                types=self.types,
                scale=self.scale,
                clean_model=self.clean_model,
                fast_physics=self.fast_physics,
                remove_doubles=self.remove_doubles,
                fix_IK_links=self.fix_IK_links,
                ik_loop_factor=self.ik_loop_factor,
                apply_bone_fixed_axis=self.apply_bone_fixed_axis,
                rename_LR_bones=self.rename_bones,
                use_underscore=self.use_underscore,
                translator=self.__translator,
                use_mipmap=self.use_mipmap,
                sph_blend_factor=self.sph_blend_factor,
                spa_blend_factor=self.spa_blend_factor,
                )
            self.report({'INFO'}, 'Imported MMD model from "%s"'%self.filepath)
        except Exception as e:
            err_msg = traceback.format_exc()
            logging.error(err_msg)
            raise
        finally:
            if self.save_log:
                logger.removeHandler(handler)

        return {'FINISHED'}

    def invoke(self, context, event):
        self.load_preferences_on_invoke(context, "default_pmx_import_preset")
        return ImportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()

class ImportVmd(Operator, ImportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.import_vmd'
    bl_label = 'Import VMD File (.vmd)'
    bl_description = 'Import a VMD file to selected objects (.vmd)'
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filename_ext = '.vmd'
    filter_glob: bpy.props.StringProperty(default='*.vmd', options={'HIDDEN'})

    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for importing the motion',
        default=0.08,
        )
    margin: bpy.props.IntProperty(
        name='Margin',
        description='How many frames added before motion starting',
        min=0,
        default=5,
        )
    bone_mapper: bpy.props.EnumProperty(
        name='Bone Mapper',
        description='Select bone mapper',
        items=[
            ('BLENDER', 'Blender', 'Use blender bone name', 0),
            ('PMX', 'PMX', 'Use japanese name of MMD bone', 1),
            ('RENAMED_BONES', 'Renamed bones', 'Rename the bone of motion data to be blender suitable', 2),
            ],
        default='PMX',
        )
    rename_bones: bpy.props.BoolProperty(
        name='Rename Bones - L / R Suffix',
        description='Use Blender naming conventions for Left / Right paired bones',
        default=True,
        )
    use_underscore: bpy.props.BoolProperty(
        name="Rename Bones - Use Underscore",
        description='Will not use dot, e.g. if renaming bones, will use _R instead of .R',
        default=False,
        )
    dictionary: bpy.props.EnumProperty(
        name='Rename Bones To English',
        items=DictionaryEnum.get_dictionary_items,
        description='Translate bone names from Japanese to English using selected dictionary',
        )
    use_pose_mode: bpy.props.BoolProperty(
        name='Treat Current Pose as Rest Pose',
        description='You can pose the model to fit the original pose of a motion data, such as T-Pose or A-Pose',
        default=False,
        options={'SKIP_SAVE'},
        )
    use_mirror: bpy.props.BoolProperty(
        name='Mirror Motion',
        description='Import the motion by using X-Axis mirror',
        default=False,
        )
    update_scene_settings: bpy.props.BoolProperty(
        name='Update scene settings',
        description='更新帧范围与帧率：勾选转换为 60 帧时使用 60 fps，否则使用 30 fps',
        default=True,
        )
    use_NLA: bpy.props.BoolProperty(
        name='Use NLA',
        description='Import the motion as NLA strips',
        default=False,
        )
    convert_to_60fps: bpy.props.BoolProperty(
        name='按 30→60 fps 转换动画时间（×2）', default=False,
        description='默认按当前工程实际帧率是否为 60 判断；以第 0 帧为基准翻倍关键帧与手柄时间，包含边距；已转换的 VMD 请取消')
    repair_camera_cuts: bpy.props.BoolProperty(
        name='修正翻倍后的镜头切换间隔', default=False,
        description='只在时间翻倍时生效；推断普通镜头相邻帧及烘焙段明显跳变，将后一关键帧前移 1 帧；可能误判，请检查结果')
    clear_previous_motion: bpy.props.BoolProperty(
        name='导入前清理角色旧动画', default=False,
        description='仅对选中的角色根空物体生效：清除旧骨骼动画并重置姿态；有关键帧的表情归零，保留未打帧的表情值')
    import_motion_audio: bpy.props.BoolProperty(name='导入动作音频', default=False,
        description='仅选择角色根空物体时生效；查找 VMD 同目录音频，新增音轨并对齐动作起点')
    replace_motion_audio: bpy.props.BoolProperty(name='替换已有音频', default=False,
        description='成功读取新音频后删除当前场景所有声音条带（含配音、音效和元条带内音频）；关闭时保留并新增音轨')
    motion_audio_choice: bpy.props.EnumProperty(name='动作音频', items=motion_audio_items,
        options={'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(
        type=OperatorFileListElement,
        )
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'scale')
        layout.prop(self, 'margin')
        layout.prop(self, 'convert_to_60fps')
        row = layout.row()
        row.enabled = self.convert_to_60fps
        row.prop(self, 'repair_camera_cuts')
        layout.label(text='VMD 第 0 帧导入位置：第 %d 帧' % ((self.margin + 1) * (2 if self.convert_to_60fps else 1)))
        from ..core.vmd.import_options import selected_model_roots
        if selected_model_roots(context):
            layout.prop(self, 'clear_previous_motion')
            if self.clear_previous_motion:
                layout.label(text='将清理骨骼、NLA 和有动画的表情', icon='INFO')
            layout.prop(self, 'import_motion_audio')
            if self.import_motion_audio:
                layout.prop(self, 'replace_motion_audio')
                if self.replace_motion_audio:
                    layout.label(text='将替换当前场景所有声音条带', icon='INFO')
                from ..audio_transfer import candidates, resolve_motion_audio, AudioError
                files = candidates(self)
                if len(files) > 1:layout.prop(self, 'motion_audio_choice')
                try:
                    audio = resolve_motion_audio(self)
                    layout.label(text='音频：' + os.path.basename(audio) if audio else '未唯一匹配音频，将跳过音频、继续导入动作')
                except AudioError:
                    layout.label(text='同目录有多个音频，请选择。', icon='INFO')
        layout.prop(self, 'use_NLA')

        layout.prop(self, 'bone_mapper')
        if self.bone_mapper == 'RENAMED_BONES':
            layout.prop(self, 'rename_bones')
            layout.prop(self, 'use_underscore')
            layout.prop(self, 'dictionary')
        layout.prop(self, 'use_pose_mode')
        layout.prop(self, 'use_mirror')

        layout.prop(self, 'update_scene_settings')

    def execute(self, context):
        from ..core.vmd.import_options import selected_model_roots, clear_model_motion
        if not self.properties.is_property_set('convert_to_60fps'):
            initialize_vmd_timing(self, context)
        audio_path = None
        if self.import_motion_audio and selected_model_roots(context):
            from ..audio_transfer import resolve_motion_audio, AudioError
            try:
                audio_path = resolve_motion_audio(self)
            except (AudioError, OSError) as exc:
                self.report({'WARNING'}, '跳过音频，继续导入动作：' + str(exc))
        roots_to_clear = selected_model_roots(context) if self.clear_previous_motion else []
        selected_objects = set(context.selected_objects)
        for i in frozenset(selected_objects):
            root = mmd_model.Model.findRoot(i)
            if root == i:
                rig = mmd_model.Model(root)
                selected_objects.add(rig.armature())
                selected_objects.add(rig.morph_slider.placeholder())
                selected_objects |= set(rig.meshes())

        bone_mapper = None
        if self.bone_mapper == 'PMX':
            bone_mapper = makePmxBoneMap
        elif self.bone_mapper == 'RENAMED_BONES':
            bone_mapper = vmd_importer.RenamedBoneMapper(
                rename_LR_bones=self.rename_bones,
                use_underscore=self.use_underscore,
                translator=DictionaryEnum.get_translator(self.dictionary),
                ).init

        for file in self.files:
            start_time = time.time()
            importer = vmd_importer.VMDImporter(
                filepath=os.path.join(self.directory, file.name),
                scale=self.scale,
                bone_mapper=bone_mapper,
                use_pose_mode=self.use_pose_mode,
                frame_margin=self.margin,
                use_mirror=self.use_mirror,
                use_NLA=self.use_NLA,
                convert_to_60fps=self.convert_to_60fps,
                repair_camera_cuts=self.repair_camera_cuts,
                )

            # Parse the first file before clearing. Reset once per batch, so
            # later files/NLA strips in the same drop do not erase earlier ones.
            for root in roots_to_clear:
                clear_model_motion(context, root)
            roots_to_clear = []
            for i in selected_objects:
                importer.assign(i)
            logging.info(' Finished importing motion in %f seconds.', time.time() - start_time)

        if self.update_scene_settings:
            auto_scene_setup.setupFrameRanges()
            auto_scene_setup.setupFps()
            if self.convert_to_60fps:
                context.scene.render.fps = 60
        context.scene.frame_set(context.scene.frame_current)
        if audio_path:
            from ..audio_transfer import AudioReplacement
            replacement = AudioReplacement(context.scene, replace_existing=self.replace_motion_audio)
            try:
                replacement.stage_file(audio_path, (self.margin + 1) * (2 if self.convert_to_60fps else 1))
                replacement.commit()
            except Exception as exc:
                replacement.rollback()
                self.report({'WARNING'}, '动作已导入，音频未导入：' + str(exc))
        return {'FINISHED'}

    def invoke(self, context, event):
        explicit = self.convert_to_60fps if self.properties.is_property_set('convert_to_60fps') else None
        self.load_preferences_on_invoke(context, "default_vmd_import_preset")
        initialize_vmd_timing(self, context, explicit)
        return ImportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()

class ImportVpd(Operator, ImportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.import_vpd'
    bl_label = 'Import VPD File (.vpd)'
    bl_description = "Import VPD file(s) to selected rig's pose library (.vpd)"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    files: bpy.props.CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: bpy.props.StringProperty(maxlen=1024, subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    filename_ext = '.vpd'
    filter_glob: bpy.props.StringProperty(default='*.vpd', options={'HIDDEN'})

    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for importing the pose',
        default=0.08,
        )
    bone_mapper: bpy.props.EnumProperty(
        name='Bone Mapper',
        description='Select bone mapper',
        items=[
            ('BLENDER', 'Blender', 'Use blender bone name', 0),
            ('PMX', 'PMX', 'Use japanese name of MMD bone', 1),
            ('RENAMED_BONES', 'Renamed bones', 'Rename the bone of pose data to be blender suitable', 2),
            ],
        default='PMX',
        )
    rename_bones: bpy.props.BoolProperty(
        name='Rename Bones - L / R Suffix',
        description='Use Blender naming conventions for Left / Right paired bones',
        default=True,
        )
    use_underscore: bpy.props.BoolProperty(
        name="Rename Bones - Use Underscore",
        description='Will not use dot, e.g. if renaming bones, will use _R instead of .R',
        default=False,
        )
    dictionary: bpy.props.EnumProperty(
        name='Rename Bones To English',
        items=DictionaryEnum.get_dictionary_items,
        description='Translate bone names from Japanese to English using selected dictionary',
        )
    use_pose_mode: bpy.props.BoolProperty(
        name='Treat Current Pose as Rest Pose',
        description='You can pose the model to fit the original pose of a pose data, such as T-Pose or A-Pose',
        default=False,
        options={'SKIP_SAVE'},
        )

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'scale')

        layout.prop(self, 'bone_mapper')
        if self.bone_mapper == 'RENAMED_BONES':
            layout.prop(self, 'rename_bones')
            layout.prop(self, 'use_underscore')
            layout.prop(self, 'dictionary')
        layout.prop(self, 'use_pose_mode')

    def execute(self, context):
        selected_objects = set(context.selected_objects)
        for i in frozenset(selected_objects):
            root = mmd_model.Model.findRoot(i)
            if root == i:
                rig = mmd_model.Model(root)
                selected_objects.add(rig.armature())
                selected_objects.add(rig.morph_slider.placeholder())
                selected_objects |= set(rig.meshes())

        bone_mapper = None
        if self.bone_mapper == 'PMX':
            bone_mapper = makePmxBoneMap
        elif self.bone_mapper == 'RENAMED_BONES':
            bone_mapper = vmd_importer.RenamedBoneMapper(
                rename_LR_bones=self.rename_bones,
                use_underscore=self.use_underscore,
                translator=DictionaryEnum.get_translator(self.dictionary),
                ).init

        for f in self.files:
            importer = vpd_importer.VPDImporter(
                filepath=os.path.join(self.directory, f.name),
                scale=self.scale,
                bone_mapper=bone_mapper,
                use_pose_mode=self.use_pose_mode,
                )
            for i in selected_objects:
                importer.assign(i)
        return {'FINISHED'}

    def invoke(self, context, event):
        self.load_preferences_on_invoke(context, "default_vpd_import_preset")
        return ImportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()

class ExportPmx(Operator, ExportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.export_pmx'
    bl_label = 'Export PMX File (.pmx)'
    bl_description = 'Export selected MMD model(s) to PMX file(s) (.pmx)'
    bl_options = {'PRESET'}

    filename_ext = '.pmx'
    filter_glob: bpy.props.StringProperty(default='*.pmx', options={'HIDDEN'})

    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for exporting the model',
        default=12.5,
        )
    copy_textures: bpy.props.BoolProperty(
        name='Copy textures',
        description='Copy textures',
        default=True,
        )
    sort_materials: bpy.props.BoolProperty(
        name='Sort Materials',
        description=('Sort materials for alpha blending. '
                     'WARNING: Will not work if you have ' +
                     'transparent meshes inside the model. ' +
                     'E.g. blush meshes'),
        default=False,
        )
    disable_specular: bpy.props.BoolProperty(
        name='Disable SPH/SPA',
        description='Disables all the Specular Map textures. It is required for some MME Shaders.',
        default=False,
        )
    visible_meshes_only: bpy.props.BoolProperty(
        name='Visible Meshes Only',
        description='Export visible meshes only',
        default=False,
        )
    overwrite_bone_morphs_from_pose_library: bpy.props.BoolProperty(
        name='Overwrite Bone Morphs',
        description='Overwrite the bone morphs from active pose library before exporting.',
        default=False,
        )
    translate_in_presets: bpy.props.BoolProperty(
        name='(Experimental) Translate in Presets',
        description='Translate in presets before exporting.',
        default=False,
        )
    sort_vertices: bpy.props.EnumProperty(
        name='Sort Vertices',
        description='Choose the method to sort vertices',
        items=[
            ('NONE', 'None', 'No sorting', 0),
            ('BLENDER', 'Blender', "Use blender's internal vertex order", 1),
            ('CUSTOM', 'Custom', 'Use custom vertex weight of vertex group "mmd_vertex_order"', 2),
            ],
        default='NONE',
        )
    log_level: bpy.props.EnumProperty(
        name='Log level',
        description='Select log level',
        items=LOG_LEVEL_ITEMS,
        default='DEBUG',
        )
    save_log: bpy.props.BoolProperty(
        name='Create a log file',
        description='Create a log file',
        default=False,
        )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj in context.selected_objects and mmd_model.Model.findRoot(obj)

    def execute(self, context):
        try:
            folder = os.path.dirname(self.filepath)
            models = {mmd_model.Model.findRoot(i) for i in context.selected_objects}
            for root in models:
                if root is None:
                    continue
                # use original self.filepath when export only one model
                # otherwise, use root object's name as file name
                if len(models) > 1:
                    model_name = bpy.path.clean_name(root.name)
                    model_folder = os.path.join(folder, model_name)
                    os.makedirs(model_folder, exist_ok=True)
                    self.filepath = os.path.join(model_folder, model_name + '.pmx')
                self._do_execute(context, root)
        except Exception as e:
            err_msg = traceback.format_exc()
            self.report({'ERROR'}, err_msg)
        return {'FINISHED'}

    def _do_execute(self, context, root):
        logger = logging.getLogger()
        logger.setLevel(self.log_level)
        if self.save_log:
            handler = log_handler(self.log_level, filepath=self.filepath + '.mmd_tools.export.log')
            logger.addHandler(handler)

        rig = mmd_model.Model(root)
        arm = rig.armature()
        if arm is None:
            self.report({'ERROR'}, '[Skipped] The armature object of MMD model "%s" can\'t be found'%root.name)
            return {'CANCELLED'}
        orig_pose_position = None
        if not root.mmd_root.is_built: # use 'REST' pose when the model is not built
            orig_pose_position = arm.data.pose_position
            arm.data.pose_position = 'REST'
            arm.update_tag()
            context.scene.frame_set(context.scene.frame_current)

        try:
            meshes = rig.meshes()
            if self.visible_meshes_only:
                meshes = (x for x in meshes if x in context.visible_objects)
            pmx_exporter.export(
                filepath=self.filepath,
                scale=self.scale,
                root=rig.rootObject(),
                armature=rig.armature(),
                meshes=meshes,
                rigid_bodies=rig.rigidBodies(),
                joints=rig.joints(),
                copy_textures=self.copy_textures,
                overwrite_bone_morphs_from_pose_library=self.overwrite_bone_morphs_from_pose_library,
                translate_in_presets=self.translate_in_presets,
                sort_materials=self.sort_materials,
                sort_vertices=self.sort_vertices,
                disable_specular=self.disable_specular,
                )
            self.report({'INFO'}, 'Exported MMD model "%s" to "%s"'%(root.name, self.filepath))
        except Exception as e:
            err_msg = traceback.format_exc()
            logging.error(err_msg)
            raise
        finally:
            if orig_pose_position:
                arm.data.pose_position = orig_pose_position
            if self.save_log:
                logger.removeHandler(handler)

        return {'FINISHED'}

    def invoke(self, context, event):
        self.load_preferences_on_invoke(context, "default_pmx_export_preset")
        return ExportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()

class ExportVmd(Operator, ExportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.export_vmd'
    bl_label = 'Export VMD File (.vmd)'
    bl_description = 'Export motion data of active object to a VMD file (.vmd)'
    bl_options = {'PRESET'}

    filename_ext = '.vmd'
    filter_glob: bpy.props.StringProperty(default='*.vmd', options={'HIDDEN'})

    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for exporting the motion',
        default=12.5,
        )
    use_pose_mode: bpy.props.BoolProperty(
        name='Treat Current Pose as Rest Pose',
        description='You can pose the model to export a motion data to different pose base, such as T-Pose or A-Pose',
        default=False,
        options={'SKIP_SAVE'},
        )
    use_frame_range: bpy.props.BoolProperty(
        name='Use Frame Range',
        description = 'Export frames only in the frame range of context scene',
        default = False,
        )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False

        if obj.mmd_type == 'ROOT':
            return True
        if obj.mmd_type == 'NONE' and (obj.type == 'ARMATURE' or getattr(obj.data, 'shape_keys', None)):
            return True
        if MMDCamera.isMMDCamera(obj) or MMDLamp.isMMDLamp(obj):
            return True

        return False

    def execute(self, context):
        params = {
            'filepath':self.filepath,
            'scale':self.scale,
            'use_pose_mode':self.use_pose_mode,
            'use_frame_range':self.use_frame_range,
            }

        obj = context.active_object
        if obj.mmd_type == 'ROOT':
            rig = mmd_model.Model(obj)
            params['mesh'] = rig.morph_slider.placeholder(binded=True) or rig.firstMesh()
            params['armature'] = rig.armature()
            params['model_name'] = obj.mmd_root.name or obj.name
        elif getattr(obj.data, 'shape_keys', None):
            params['mesh'] = obj
            params['model_name'] = obj.name
        elif obj.type == 'ARMATURE':
            params['armature'] = obj
            params['model_name'] = obj.name
        else:
            for i in context.selected_objects:
                if MMDCamera.isMMDCamera(i):
                    params['camera'] = i
                elif MMDLamp.isMMDLamp(i):
                    params['lamp'] = i

        try:
            start_time = time.time()
            vmd_exporter.VMDExporter().export(**params)
            logging.info(' Finished exporting motion in %f seconds.', time.time() - start_time)
        except Exception as e:
            err_msg = traceback.format_exc()
            logging.error(err_msg)
            self.report({'ERROR'}, err_msg)

        return {'FINISHED'}

    def invoke(self, context, event):
        self.load_preferences_on_invoke(context, "default_vmd_export_preset")
        return ExportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()

class ExportVpd(Operator, ExportHelper, PreferencesMixin):
    bl_idname = 'mmd_tools.export_vpd'
    bl_label = 'Export VPD File (.vpd)'
    bl_description = 'Export to VPD file(s) (.vpd)'
    bl_description = "Export active rig's pose library to VPD file(s) (.vpd)"
    bl_options = {'PRESET'}

    filename_ext = '.vpd'
    filter_glob: bpy.props.StringProperty(default='*.vpd', options={'HIDDEN'})

    scale: bpy.props.FloatProperty(
        name='Scale',
        description='Scaling factor for exporting the pose',
        default=12.5,
        )
    pose_type: bpy.props.EnumProperty(
        name='Pose Type',
        description='Choose the pose type to export',
        items=[
            ('CURRENT', 'Current Pose', 'Current pose of the rig', 0),
            ('ACTIVE', 'Active Pose', "Active pose of the rig's pose library", 1),
            ('ALL', 'All Poses', "All poses of the rig's pose library (the pose name will be the file name)", 2),
            ],
        default='CURRENT',
        )
    use_pose_mode: bpy.props.BoolProperty(
        name='Treat Current Pose as Rest Pose',
        description='You can pose the model to export a pose data to different pose base, such as T-Pose or A-Pose',
        default=False,
        options={'SKIP_SAVE'},
        )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None:
            return False

        if obj.mmd_type == 'ROOT':
            return True
        if obj.mmd_type == 'NONE' and (obj.type == 'ARMATURE' or getattr(obj.data, 'shape_keys', None)):
            return True

        return False

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'scale')
        layout.prop(self, 'pose_type', expand=True)
        if self.pose_type != 'CURRENT':
            layout.prop(self, 'use_pose_mode')

    def execute(self, context):
        params = {
            'filepath':self.filepath,
            'scale':self.scale,
            'pose_type':self.pose_type,
            'use_pose_mode':self.use_pose_mode,
            }

        obj = context.active_object
        if obj.mmd_type == 'ROOT':
            rig = mmd_model.Model(obj)
            params['mesh'] = rig.morph_slider.placeholder(binded=True) or rig.firstMesh()
            params['armature'] = rig.armature()
            params['model_name'] = obj.mmd_root.name or obj.name
        elif getattr(obj.data, 'shape_keys', None):
            params['mesh'] = obj
            params['model_name'] = obj.name
        elif obj.type == 'ARMATURE':
            params['armature'] = obj
            params['model_name'] = obj.name

        try:
            vpd_exporter.VPDExporter().export(**params)
        except Exception as e:
            err_msg = traceback.format_exc()
            logging.error(err_msg)
            self.report({'ERROR'}, err_msg)
        return {'FINISHED'}

    def invoke(self, context, event):
        self.load_preferences_on_invoke(context, "default_vpd_export_preset")
        return ExportHelper.invoke(self, context, event)

    def cancel(self, context):
        self.restore_preferences_on_cancel()
