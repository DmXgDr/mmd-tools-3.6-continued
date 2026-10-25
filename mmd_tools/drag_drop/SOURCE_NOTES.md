# Source notes / license

GPL-3.0-or-later. License text: GPL-3.0.txt.

This is an independent interaction backport for Blender 3.6, not an official MMD Tools release.

- Behavior reference: MMD-Blender/blender_mmd_tools v4.5.14, mmd_tools/operators/fileio.py (FileHandler declarations and invoke methods).
- Blender reference: blender/blender v4.5.6, scripts/modules/bpy_extras/io_utils.py (ImportHelper.invoke_popup and poll_file_object_drop).
- Integrated runtime: MMD Tools v3.0.0 for Blender 3.6, based on upstream 2.10.3. MMD Tools authors retain copyright in its property/draw/import implementations.
- win_drop.py uses a process-local Windows OLE forwarding bridge, not Blender 4.x FileHandler.
- The interaction and operator wrapper are implemented here. This is not a complete port of the newer import engine or its action-merging/current-frame behavior.
- Public scope and other upstream references: ../docs/UPSTREAM.md.
