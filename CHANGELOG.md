# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.1.8] - 2026-05-02

### Added
- 新增插件 Logo。

## [0.1.7] - 2026-03-31

### Security
- **管理员权限扩展**：`list_skill_files`、`read_skill_file` 工具和 `/skill files`、`/skill read` 命令新增管理员校验
- **MCP 名称校验**：`/mcp config`、`/mcp update` 命令新增 `_MCP_NAME_RE` 名称白名单校验
- **配置持久化校验**：`mcp_on`、`mcp_off`、`mcp_del` 命令检查 `save_mcp_config()` 返回值，保存失败时报错
- **MCP 更新回滚**：`/mcp update` 命令和 `UpdateMcpServerTool` 启用失败时自动恢复旧配置并重新启用旧服务
- **原子 ZIP 更新**：`_validate_and_update_from_zip` 在覆盖前备份现有文件，复制失败时自动回滚
- **ZIP 名称预校验**：`UpdateSkillFromZipTool` 在覆盖前验证 ZIP 内 Skill 名与目标一致，防止误写
- **敏感信息脱敏增强**：`_mask_sensitive_config` / `_mask_sensitive` 递归处理嵌套列表
- **错误信息脱敏**：所有工具的异常消息改为通用描述，内部细节仅输出到日志
- **Diff 性能防护**：`target_content` 设有 50000 字符长度上限，防止大文本拖慢

### Changed
- **纯文本状态标签**：移除所有 emoji，使用 `[成功]` `[失败]` `[警告]` `[运行中]` `[已启用]` `[已禁用]` 等纯文本标签
- **Diff 编辑提示**：工具描述中新增 `target_content` 长度上限提示，引导 AI 分次调用
- **批量文件处理**：`/skill install` 和 `/skill update` 支持单消息包含多个 ZIP 文件

### Fixed
- `/skill install`、`/skill update` 命令处理同一消息中的所有附件文件，不再只取第一个

## [0.1.6] - 2026-03-26

### Added
- **沙盒环境适配**：`install_skill` 和 `update_skill_from_zip` 支持沙盒路径，自动通过 `ComputerBooter.download_file()` 静默下载到主机安装
- **运行时感知**：`list_skills` 根据 `computer_use_runtime` 传入 `runtime` 参数，沙盒模式下正确展示沙盒 Skills
- **安装后同步**：Skill 安装/更新后自动触发 `sync_skills_to_active_sandboxes()` 推送到活跃沙盒
- **`skill_name_hint` 参数**：`install_skill` 新增可选参数，允许指定安装后的 Skill 名称（适配 AstrBot PR #6952）
- **网络安装指南**：SKILL.md 新增「从网络链接安装 Skill 或 MCP 服务器」章节，覆盖沙盒/本地模式下的 URL 安装流程

### Changed
- `_SKILL_NAME_RE` 从 `^[A-Za-z0-9._-]+$` 更新为 `^[\w.-]+$`，支持中文 Skill 名称（适配 AstrBot PR #6952）
- `install_skill` 描述更新：ZIP 支持根目录直接包含 SKILL.md 的结构

### Fixed
- `install_skill` 新增 `FileExistsError` 处理，重复安装时返回友好提示而非通用异常

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
