# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.5] - 2026-03-14

### Added
- **编辑模式**：`update_skill_file` 和 `update_mcp_server` 工具支持 IDE 风格的 diff 编辑
  - AI 提供 `target_content` + `replacement_content`，系统验证匹配度后执行替换
  - 支持精确匹配快捷路径和 `SequenceMatcher` 模糊匹配
  - MCP 配置 diff 在 JSON 字符串上操作，替换后自动验证格式 + 测试连接
- 新增 `_conf_schema.json` 插件配置：
  - `diff_mode`：编辑模式开关（默认开启）
  - `diff_match_threshold`：匹配阈值滑块（50-100%，默认 100%）

### Fixed
- `mcp_on`/`EnableMcpServerTool`：先连接后保存，避免连接失败时配置已被标记为启用
- `mcp_off`/`DisableMcpServerTool`：先断开后保存，避免断开失败时配置已被标记为禁用
- `mcp_add`：启用失败时回滚已保存的配置
- `UpdateMcpServerTool`：检查 `save_mcp_config` 返回值，保存失败时报错

### Security
- **路径遍历修复**：`skill_files`/`skill_read`/`skill_update` 添加 `_SKILL_NAME_RE` 名称校验
- **路径边界加固**：`ReadSkillFileTool`/`UpdateSkillFileTool` 从 `str.startswith(skills_root)` 改为 `relative_to(skill_dir)`，防止前缀混淆和跨 Skill 访问
- **Zip Slip 防护**：`_validate_and_update_from_zip` 在解压前验证所有 ZIP 成员路径
- **单文件写入路径约束**：`skill_update` 单文件模式添加 `resolve()` + `relative_to(skill_dir)` 检查
- **ZIP 名称一致性**：`UpdateSkillFromZipTool` 验证 ZIP 内 Skill 名与目标一致，不一致时回滚
- **私有符号解耦**：`_SKILL_NAME_RE` 改为本地定义，不再依赖 `skill_manager` 私有导出
- **错误信息规范化**：MCP 工具错误返回 `type(e).__name__: e` 格式，避免泄露原始异常栈

### Changed
- 文件计数改为递归统计实际文件数（`rglob`），不再只计顶层条目

<details>
<summary>历史版本</summary>

## [0.1.0] - 2026-03-12

### Added
- 9 个 Skills 管理 LLM Tool（列表、启用、禁用、删除、安装、文件列表、读取、更新、ZIP 更新）
- 7 个 MCP 服务器管理 LLM Tool（列表、配置查看、启用、禁用、添加、更新、移除）
- `/skill` 命令组：用户直接通过指令管理 Skills
- `/mcp` 命令组：用户直接通过指令管理 MCP 服务器
- 内置 Skill `skills-mcp-manager`：AI 指令手册
- 管理员权限校验
- MCP 配置自动脱敏
- 连接测试和二次确认机制
- GitHub Issue 模板和 CI 工作流

</details>
