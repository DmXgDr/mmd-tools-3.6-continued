# 来源、移植范围与实现对应

比较基线为原版 MMD Tools 2.10.3。高版本移植的代码参考固定在 **v4.5.14**，不是声称与未来的 main 分支始终一致。以下链接用于追溯功能来源；3.6 适配后的内部实现可能不同。

## 高版本移植及参考

| 功能 | 上游依据 | 本版实现与边界 |
| --- | --- | --- |
| 默认操作预设 | [默认预设提交](https://github.com/MMD-Blender/blender_mmd_tools/commit/09444e24840dc51c0fb12a08ba1fa8891967bbdd)、[偏好设置提交](https://github.com/MMD-Blender/blender_mmd_tools/commit/b93b75e6d141fc26a0d3275953e3a9865ef63fa9)、[预设解析改进](https://github.com/MMD-Blender/blender_mmd_tools/commit/d39ad1604c47a6798565f27dad7368c8d7ed6af1)、[v4.5.14 fileio.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/operators/fileio.py) | `operators/fileio.py`、`preferences.py`；`preset_store.py` 的名称持久化、内容备份和迁移隔离是本版扩展 |
| 拖放后弹出原导入设置 | [v4.5.14 fileio.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/operators/fileio.py) | 参考其交互与导入区域规则；`drag_drop/` 为 Windows/3.6 接收及队列实现，不是直接使用高版本 FileHandler |
| 骨骼名称检查修复 | [v4.5.14 model_validation.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/operators/model_validation.py)、[替换禁用字符后的重名修复](https://github.com/MMD-Blender/blender_mmd_tools/commit/7af9913c323c17667da67a6264b0650ab62fb586) | `operators/bone_validation.py`、`panels/bone_debug.py`、OpenCC 依赖；只移植骨骼名称部分，不包括整模型/表情/贴图检查 |
| 附加旋转修复 | [PR #292](https://github.com/MMD-Blender/blender_mmd_tools/pull/292)、[v4.5.14 bone.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/core/bone.py) | `core/bone.py` 约束旋转计算的 3.6 适配，`operators/model.py` 提供旧模型更新入口 |
| 按贴图合并材质 | [PR #249](https://github.com/MMD-Blender/blender_mmd_tools/pull/249)、[后续路径比较改进](https://github.com/MMD-Blender/blender_mmd_tools/commit/a0f6b39178f6a374c02d1ca42fb21c429cc166ff)、[v4.5.14 material.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/operators/material.py) | `operators/material.py`、`panels/tool.py`；只按符合条件的图像路径合并，不做视觉等价比较 |
| 表情滑块与名称显示 | [PR #257](https://github.com/MMD-Blender/blender_mmd_tools/pull/257)、[相关滑块显示修正](https://github.com/MMD-Blender/blender_mmd_tools/commit/5527f44f20f7bea2985eddf002fe7963d6be53d0) | `panels/sidebar.py`、`properties/root.py`；保留 3.6 表情绑定方式 |
| 为全部表情注册关键帧 | [PR #329](https://github.com/MMD-Blender/blender_mmd_tools/pull/329)、[v4.5.14 morph.py](https://github.com/MMD-Blender/blender_mmd_tools/blob/v4.5.14/mmd_tools/operators/morph.py) | `operators/morph.py`、`core/morph.py`；遍历已绑定控制滑块，包含跳过未变化及容差 |

## 本分支定制功能与修复

| 修改 | 实现文件 |
| --- | --- |
| 场景帧率感知的 VMD ×2 时间转换，包含手柄、边距和新导入动作的时间处理 | `operators/fileio.py`、`core/vmd/importer.py`、`core/vmd/import_options.py` |
| 稀疏/逐帧镜头的可选切换间隔修正 | `core/vmd/import_options.py`、`core/vmd/importer.py` |
| 根对象旧动画清理、共享数据保护、自动插帧恢复 | `core/vmd/import_options.py` |
| 动作同目录音频查找、歧义跳过、显式新增/替换、失败回退 | `audio_transfer.py`、`operators/fileio.py` |
| 物理创建性能优化及常规路径开关 | `core/model.py`、`core/pmx/importer.py`、`operators/fileio.py` |
| Blender 3.6 VPD 独立姿态库、姿态导出及骨骼表情转换兼容修复（本分支实现） | `core/pose_library.py`、`core/vpd/`、`core/morph.py`、`operators/fileio.py` |
| 恒定区间 VMD 导出冗余补帧修复 | `core/vmd/exporter.py` |
| Windows 拖放接收、会话设置、偏好开关及生命周期管理 | `drag_drop/__init__.py`、`drag_drop/win_drop.py`、`preferences.py` |
| 公开版默认预设保存与旧个人选择隔离 | `preset_store.py`、`preferences.py` |
| 模块注册、兼容性描述、中文界面、依赖加载 | `__init__.py`、`auto_load.py`、`m17n.py`、配套数据目录 |


## 保留的原版功能

模型、动作与姿态基础读写、材质着色、物理模拟、表情绑定、NLA 支持等仍以 2.10.3 为基础。没有移植整个 4.5.14 插件，也没有将高版本所有已修复问题一次性带入 3.6。未列出的上游后续功能不能据此视为已支持。
