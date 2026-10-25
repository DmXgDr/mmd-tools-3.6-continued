"""Transactional VSE sound replacement for camera reuse and VMD imports."""
import hashlib
import os
import tempfile
import wave
from collections import Counter
import bpy


class AudioError(Exception):
    pass


def sound_path(sound):
    return os.path.realpath(bpy.path.abspath(sound.filepath, library=sound.library))


def sound_digest(sound, cache):
    if sound.packed_file:
        return hashlib.sha256(sound.packed_file.data).hexdigest()
    path = os.path.normcase(sound_path(sound))
    if path not in cache:
        digest = hashlib.sha256()
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        cache[path] = digest.hexdigest()
    return cache[path]


def sounds(scene):
    """Return the actual owner collection so sounds inside metas can be removed."""
    def walk(collection, parents=()):
        for strip in collection:
            if strip.type == 'SOUND':
                yield collection, strip, parents
            elif strip.type == 'META':
                yield from walk(strip.sequences, parents + (strip,))
    return list(walk(scene.sequence_editor.sequences)) if scene.sequence_editor else []


def same_audio(source, target):
    if not source or len(source) != len(target):
        return False
    cache = {}
    try:
        return Counter(sound_digest(s.sound, cache) for _, s, _ in source) == Counter(
            sound_digest(s.sound, cache) for _, s, _ in target)
    except (OSError, AttributeError):
        return False


def copy_curve(source, action, path):
    fc = action.fcurves.new(path, index=source.array_index)
    for prop in ('extrapolation', 'mute', 'lock', 'color_mode', 'auto_smoothing'):
        setattr(fc, prop, getattr(source, prop))
    fc.color = source.color
    if source.sampled_points:
        raise AudioError('音频使用采样曲线，请先转换为关键帧后再复用。')
    fc.keyframe_points.add(len(source.keyframe_points))
    for src, dst in zip(source.keyframe_points, fc.keyframe_points):
        dst.co = src.co; dst.interpolation = src.interpolation; dst.easing = src.easing
        dst.handle_left_type = src.handle_left_type; dst.handle_right_type = src.handle_right_type
        dst.handle_left = src.handle_left; dst.handle_right = src.handle_right
        for prop in ('amplitude', 'back', 'period', 'type'):
            setattr(dst, prop, getattr(src, prop))
    for src in source.modifiers:
        if src.type == 'ENVELOPE':
            raise AudioError('音频曲线含包络修改器，当前无法无损复制。')
        dst = fc.modifiers.new(src.type)
        for prop in src.bl_rna.properties:
            if prop.identifier == 'rna_type' or prop.is_readonly or prop.type in {'COLLECTION', 'POINTER'}:
                continue
            setattr(dst, prop.identifier, getattr(src, prop.identifier))
    fc.update()


class AudioReplacement:
    def __init__(self, scene, replace_existing=True):
        self.scene = scene
        self.old = sounds(scene) if replace_existing else []
        self.created = []
        self.new_sounds = []
        self.editor_created = scene.sequence_editor is None
        self.original_action = scene.animation_data.action if scene.animation_data else None
        self.new_action = None
        self.status = 'disabled'
        self.count = 0

    def _make(self, sound, name, frame, channel):
        # A tiny temporary file only bootstraps the strip. Replace its sound ID
        # with a local copy, which preserves packed bytes even if disk is missing.
        editor = self.scene.sequence_editor_create()
        with tempfile.TemporaryDirectory(prefix='mmd-audio-', dir=bpy.app.tempdir or None) as folder:
            path = os.path.join(folder, 'placeholder.wav')
            with wave.open(path, 'wb') as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(48000)
                stream.writeframes(b'\0\0' * 480)
            strip = editor.sequences.new_sound(name, path, channel=channel, frame_start=int(frame))
            self.created.append(strip)
            stub = strip.sound
            local = sound.copy(); self.new_sounds.append(local)
            local.filepath = sound_path(sound)
            strip.sound = local
            # Recalculate native media length after replacing the bootstrap ID.
            strip.speed_factor = 1.0
            strip.animation_offset_start = 0
            strip.animation_offset_end = 0
            if stub.users == 0:
                bpy.data.sounds.remove(stub)
        strip.frame_start = frame
        return strip

    def _channel(self, preferred, start, end):
        # Old audio will be removed; videos and newly staged audio remain.
        old = {s.as_pointer() for _, s, _ in self.old}
        occupied = list(self.scene.sequence_editor.sequences) if self.scene.sequence_editor else []
        for channel in [preferred] + [i for i in range(1, 129) if i != preferred]:
            if not any(s.as_pointer() not in old and s.channel == channel and
                       s.frame_final_start < end and s.frame_final_end > start for s in occupied):
                return channel
        raise AudioError('序列编辑器没有可用音频通道，请先整理条带。')

    def stage_scene(self, source):
        entries = sounds(source)
        if not entries:
            self.status = 'no_audio'; return
        if same_audio(entries, self.old):
            self.status = 'same_file'; return
        prefixes = [s.path_from_id() + '.' for _, s, _ in entries]
        ad = source.animation_data
        if ad and (any(fc.data_path.startswith(tuple(prefixes)) for fc in ad.drivers) or any(
                fc.data_path.startswith(tuple(prefixes)) for t in ad.nla_tracks for st in t.strips
                if st.action for fc in st.action.fcurves)):
            raise AudioError('来源音频使用驱动或 NLA，当前无法无损复制；可关闭音频选项仅复用镜头。')
        for _, src, parents in entries:
            if not src.sound or (not src.sound.packed_file and not os.path.isfile(sound_path(src.sound))):
                raise AudioError('来源音频文件丢失：' + src.name)
            if getattr(src.sound, 'animation_data', None):
                raise AudioError('来源声音数据含动画，当前无法无损复制：' + src.name)
            start = max([src.frame_final_start] + [p.frame_final_start for p in parents])
            end = min([src.frame_final_end] + [p.frame_final_end for p in parents])
            if end <= start:
                continue
            channel = self._channel(src.channel, start, end)
            dst = self._make(src.sound, src.name, src.frame_start, channel)
            for prop in ('animation_offset_start', 'animation_offset_end', 'speed_factor',
                         'volume', 'pan', 'mute', 'show_waveform', 'color_tag'):
                setattr(dst, prop, getattr(src, prop))
            dst.frame_offset_start = src.frame_offset_start
            dst.frame_offset_end = src.frame_offset_end
            dst.frame_final_start = start; dst.frame_final_end = end
            if parents:
                parent_paths = tuple(p.path_from_id() + '.' for p in parents)
                if ad and ((ad.action and any(f.data_path.startswith(parent_paths) for f in ad.action.fcurves)) or
                           any(f.data_path.startswith(parent_paths) for f in ad.drivers) or
                           any(f.data_path.startswith(parent_paths) for t in ad.nla_tracks for st in t.strips
                               if st.action for f in st.action.fcurves)):
                    raise AudioError('音频复合条带含动画，当前无法无损复制。')
                dst.mute = dst.mute or any(p.mute for p in parents)
                owner = source.sequence_editor
                for parent in parents:
                    if parent.channel < len(owner.channels) and owner.channels[parent.channel].mute:
                        dst.mute = True
                    owner = parent
            channels = parents[-1].channels if parents else source.sequence_editor.channels
            if src.channel < len(channels) and channels[src.channel].mute:
                dst.mute = True
            if ad and ad.action:
                prefix = src.path_from_id() + '.'
                for fc in ad.action.fcurves:
                    if fc.data_path.startswith(prefix):
                        if self.new_action is None:
                            self.new_action = self.original_action.copy() if self.original_action else bpy.data.actions.new('复用音频设置')
                            self.scene.animation_data_create().action = self.new_action
                        copy_curve(fc, self.new_action, dst.path_from_id() + fc.data_path[len(src.path_from_id()):])
            dst.select = False
        self.count = len(self.created)
        self.status = 'replaced' if self.created else 'no_audio'

    def stage_file(self, path, frame):
        sound = bpy.data.sounds.load(path, check_existing=False)
        try:
            # Blender must be able to decode this file before old strips go away.
            dst = self._make(sound, os.path.basename(path), frame, self._channel(1, frame, 1048574))
            if dst.frame_duration <= 0:
                raise AudioError('音频文件无法读取：' + path)
            dst.show_waveform = True; dst.select = False
        finally:
            if sound.users == 0:
                bpy.data.sounds.remove(sound)
        self.status = 'replaced'; self.count = 1

    def commit(self):
        if self.status != 'replaced':
            return
        prefixes = tuple(s.path_from_id() + '.' for _, s, _ in self.old)
        if prefixes:
            from .core.vmd.import_options import _remove_channels
            _remove_channels(self.scene, lambda fc: fc.data_path.startswith(prefixes))
            ad = self.scene.animation_data
            if ad:
                for fc in list(ad.drivers):
                    if fc.data_path.startswith(prefixes):ad.drivers.remove(fc)
        for owner, strip, _ in self.old:
            owner.remove(strip)

    def rollback(self):
        editor = self.scene.sequence_editor
        for strip in self.created:
            editor.sequences.remove(strip)
        if self.new_action:
            self.scene.animation_data.action = self.original_action
            if self.new_action.users == 0:bpy.data.actions.remove(self.new_action)
        for sound in self.new_sounds:
            if sound.users == 0:bpy.data.sounds.remove(sound)
        if self.editor_created and editor and not editor.sequences:
            self.scene.sequence_editor_clear()


AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.aif', '.aiff', '.wma'}
_enum_cache = {}


def motion_files(operator):
    paths = [os.path.join(operator.directory, f.name) for f in operator.files]
    return paths or ([operator.filepath] if operator.filepath else [])


def candidates(operator):
    paths = motion_files(operator)
    result = []
    for folder in sorted({os.path.dirname(os.path.abspath(p)) for p in paths}):
        if os.path.isdir(folder):
            result.extend(os.path.join(folder, n) for n in os.listdir(folder)
                          if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS and os.path.isfile(os.path.join(folder, n)))
    return sorted(set(result), key=str.casefold)


def motion_audio_items(operator, context):
    files = tuple(candidates(operator))
    if files not in _enum_cache:
        _enum_cache[files] = [('AUTO', '自动匹配', '优先同名；只有一个音频时自动使用', 0)] + [
            (hashlib.sha256(p.encode('utf-8')).hexdigest(), os.path.basename(p), p, i + 1) for i, p in enumerate(files)]
    return _enum_cache[files]


def resolve_motion_audio(operator):
    files = candidates(operator)
    if operator.motion_audio_choice != 'AUTO':
        selected = next((p for p in files if hashlib.sha256(p.encode('utf-8')).hexdigest() == operator.motion_audio_choice), None)
        if not selected:return None
        return selected
    stems = {os.path.splitext(os.path.basename(p))[0].casefold() for p in motion_files(operator)}
    matched = [p for p in files if os.path.splitext(os.path.basename(p))[0].casefold() in stems]
    if len(matched) == 1:return matched[0]
    if len(files) == 1:return files[0]
    # Ambiguous audio is optional; it must never cancel a valid motion import.
    return None
