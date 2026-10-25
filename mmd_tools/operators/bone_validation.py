# Copyright 2025 MMD Tools authors. GPL-3.0-or-later.
# Bone-only backport from v4.5.14 operators/model_validation.py.
import logging
import os
import bpy
from bpy.types import Operator
from ..core.model import Model
from .._vendor.opencc import OpenCC
from ..preferences import get_additional_unallowed_chars, get_replacement_char

class FnModel:
    find_root_object = staticmethod(Model.findRoot)

    @staticmethod
    def find_armature_object(root):
        return Model(root).armature() if root is not None else None

cc_s2t = OpenCC("s2t")
cc_t2jp = OpenCC(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "opencc", "t2jp")))

def _find_additional_unallowed_chars(name: str, unallowed: str) -> list[str]:
    """Return sorted list of unallowed characters found in name (deduplicated)."""
    return sorted({ch for ch in unallowed if ch in name})


def _replace_additional_unallowed_chars(name: str, unallowed: str, replacement: str) -> str:
    """Replace each unallowed character in name with replacement (or remove if empty)."""
    for ch in unallowed:
        name = name.replace(ch, replacement)
    return name


def register():
    bpy.types.Scene.mmd_validation_results = bpy.props.StringProperty(
        name="Validation Results",
        default="",
    )


def unregister():
    if hasattr(bpy.types.Scene, "mmd_validation_results"):
        del bpy.types.Scene.mmd_validation_results


def log_message(prefix, message, level="INFO"):
    """Log message with prefix for each line at the specified level.

    Args:
        prefix (str): Prefix for the log message.
        message (str): Message to log.
        level (str): Log level ('INFO', 'WARNING', 'ERROR').
    """
    logger = logging.getLogger()
    original_level = logger.level
    # Set to DEBUG to ensure all messages are output.
    logger.setLevel(logging.DEBUG)

    try:
        level = level.upper()
        for line in message.split("\n"):
            if level == "WARNING":
                logging.warning("[%s] %s", prefix, line)
            elif level == "ERROR":
                logging.error("[%s] %s", prefix, line)
            else:  # Default to INFO
                logging.info("[%s] %s", prefix, line)
    finally:
        logger.setLevel(original_level)


def force_ui_update(context):
    """Force all areas in all windows to redraw."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class MMDModelValidateBones(Operator):
    """Check MMD model bones for encoding issues and name length limits"""

    bl_idname = "mmd_tools.validate_bones"
    bl_label = "Validate Bone Limits"
    bl_description = "Check MMD model bones for encoding issues and name length limits"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = FnModel.find_root_object(context.active_object)
        if root is None:
            self.report({"ERROR"}, "No MMD model selected")
            log_message("MMD Validation", "No MMD model selected", "ERROR")
            return {"CANCELLED"}

        armature = FnModel.find_armature_object(root)
        if armature is None:
            self.report({"ERROR"}, "No armature found in model")
            log_message("MMD Validation", "No armature found in model", "ERROR")
            return {"CANCELLED"}

        issues = []
        # Set to track bone names for duplicate checking
        bone_names = set()

        for pose_bone in armature.pose.bones:
            # Skip shadow bones
            is_shadow = getattr(pose_bone, "is_mmd_shadow_bone", False) is True
            if is_shadow:
                continue

            name_j = pose_bone.mmd_bone.name_j

            # Check for duplicates
            if name_j in bone_names:
                issues.append(f"Duplicate bone name: '{name_j}'")
            else:
                bone_names.add(name_j)

            # Check cp932 encoding and length
            try:
                encoded_name = name_j.encode("cp932")
                if len(encoded_name) > 15:
                    issues.append(f"Bone '{name_j}' exceeds 15 bytes in cp932")
            except UnicodeEncodeError:
                issues.append(f"Bone '{name_j}' contains characters that cannot be encoded in cp932")

        # Check additional unallowed characters
        unallowed = get_additional_unallowed_chars("bone")
        if unallowed:
            for pose_bone in armature.pose.bones:
                if getattr(pose_bone, "is_mmd_shadow_bone", False):
                    continue
                name_j = pose_bone.mmd_bone.name_j
                found = _find_additional_unallowed_chars(name_j, unallowed)
                if found:
                    issues.append(f"Bone '{name_j}' contains additional unallowed characters: {found}")

        results = "\n".join(issues) or "No bone issues found"
        context.scene.mmd_validation_results = results
        log_level = "WARNING" if issues else "INFO"
        log_message("MMD Bone Validation", results, log_level)

        return {"FINISHED"}


class MMDModelFixBoneIssues(Operator):
    """Automatically fix bone name encoding and length issues"""

    bl_idname = "mmd_tools.fix_bone_issues"
    bl_label = "Fix Bone Issues"
    bl_description = "Fix bone name encoding issues automatically"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = FnModel.find_root_object(context.active_object)
        if root is None:
            self.report({"ERROR"}, "No MMD model selected")
            log_message("MMD Fix", "No MMD model selected", "ERROR")
            return {"CANCELLED"}

        armature = FnModel.find_armature_object(root)
        if armature is None:
            self.report({"ERROR"}, "No armature found in model")
            log_message("MMD Fix", "No armature found in model", "ERROR")
            return {"CANCELLED"}

        fixed = []
        name_counts = {}
        processed_names = set()

        # First collect all names and mark duplicates
        for pose_bone in armature.pose.bones:
            if getattr(pose_bone, "is_mmd_shadow_bone", False):
                continue
            name_j = pose_bone.mmd_bone.name_j
            name_counts[name_j] = name_counts.get(name_j, 0) + 1

        # Process all bones - fix both length and duplicates
        for pose_bone in armature.pose.bones:
            if getattr(pose_bone, "is_mmd_shadow_bone", False):
                continue

            original_name = pose_bone.mmd_bone.name_j

            # Check if name is too long in cp932
            name_too_long = False
            has_non_japanese = False
            try:
                encoded = original_name.encode("cp932")
                name_too_long = len(encoded) > 15
            except UnicodeEncodeError:
                has_non_japanese = True

            is_duplicate = original_name in processed_names or name_counts.get(original_name, 0) > 1

            # Only process bones that need fixing
            if not (name_too_long or has_non_japanese or is_duplicate):
                processed_names.add(original_name)
                continue

            # First convert/remove non-Japanese characters
            converted_name = cc_s2t.convert(original_name)
            converted_name = cc_t2jp.convert(converted_name)

            new_name = ""
            for char in converted_name:
                try:
                    char.encode("cp932")
                    new_name += char
                except UnicodeEncodeError:
                    continue

            # If name becomes empty after filtering, use a default name
            if not new_name:
                new_name = "bone"

            # Generate a unique name within the 15-byte CP932 limit
            suffix = 1
            while True:
                suffix_str = "" if suffix == 1 else str(suffix)
                max_bytes = 15 - len(suffix_str)
                temp_base = new_name
                while temp_base:
                    try:
                        if len(temp_base.encode("cp932")) <= max_bytes:
                            break
                        temp_base = temp_base[:-1]
                    except UnicodeEncodeError:
                        temp_base = temp_base[:-1]
                test_name = f"{temp_base}{suffix_str}"
                if test_name not in processed_names:
                    final_name = test_name
                    break
                suffix += 1

            # Apply the final name and update processed_names
            pose_bone.mmd_bone.name_j = final_name
            processed_names.add(final_name)

            # Only add one message per bone, showing the original and final name
            if original_name != final_name:
                fixed.append(f"Fixed bone name: '{original_name}' -> '{final_name}'")

        # Fix additional unallowed characters
        unallowed = get_additional_unallowed_chars("bone")
        if unallowed:
            replacement = get_replacement_char()
            # Collect names of bones that won't be modified, as the baseline for uniqueness check
            additional_used_names = set()
            for pb in armature.pose.bones:
                if getattr(pb, "is_mmd_shadow_bone", False):
                    continue
                additional_used_names.add(pb.mmd_bone.name_j)

            for pose_bone in armature.pose.bones:
                if getattr(pose_bone, "is_mmd_shadow_bone", False):
                    continue
                old_name = pose_bone.mmd_bone.name_j
                new_name = _replace_additional_unallowed_chars(old_name, unallowed, replacement)
                if new_name == old_name:
                    continue

                # Resolve collision
                additional_used_names.discard(old_name)
                candidate = new_name
                suffix = 2
                while candidate in additional_used_names:
                    candidate = f"{new_name}{suffix}"
                    suffix += 1
                pose_bone.mmd_bone.name_j = candidate
                additional_used_names.add(candidate)
                fixed.append(f"Fixed bone additional unallowed chars: '{old_name}' -> '{candidate}'")

        results = "\n".join(fixed) if fixed else "No bone issues to fix"
        context.scene.mmd_validation_results = results
        log_message("MMD Bone Fix", results, "INFO")

        force_ui_update(context)
        return {"FINISHED"}
