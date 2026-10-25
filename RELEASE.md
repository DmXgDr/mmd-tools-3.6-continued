# MMD Tools 3.6 非官方续更版——高版本功能移植与个人定制

**版本：MMD Tools v3.0.0**

**续更维护：晓光忆时** · 基于 sugiany / UuuNyaa / MMD Tools 贡献者的工作。

这是面向 Blender 3.6 的 MMD Tools 非官方续更版。以原版 2.10.3 为基础，移植部分高版本功能，并加入按个人使用需求编写的功能和修复。

移植内容包括默认操作预设、骨骼名称检查修复、附加旋转修复、材质合并、表情滑块显示和全部表情批量插帧；另有 Windows 拖放适配、VMD 时间转换、可选旧动画清理、动作音频、物理导入提速和部分导出冗余帧修复。

在线更新已连接本续更版仓库，可在插件偏好设置中检查并安装新的正式发行版。

## 下载与安装

- 安装包：`MMD-Tools-3.6-Continued-v3.0.0.zip`。
- 源码包：`MMD-Tools-3.6-Continued-Source-v3.0.0.zip`。
- 在 Blender 3.6 的「偏好设置 → 插件 → 安装」中选择安装 ZIP，启用「MMD Tools 3.6 续更版」，安装或升级后重启。

沿用 `mmd_tools` 模块名，作为原版的替换版本使用。包内不含个人预设。

这不是高版本 MMD Tools 的完整移植。拖放仅支持 Windows；会影响旧动画和原有音频的新增选项默认关闭。实际验证环境为 Windows / Blender 3.6.2 + SSGI Native。

[阅读完整图文教程与功能差异](README.md) · [查看各项 GitHub 来源](docs/UPSTREAM.md)
