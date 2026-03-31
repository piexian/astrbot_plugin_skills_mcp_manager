"""Skills & MCP Manager Plugin - main entry point.

Provides LLM tool interfaces and user commands for managing AstrBot Skills and MCP servers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .tools.utils import mask_sensitive
from .tools import (
    AddMcpServerTool,
    DeleteSkillTool,
    DisableMcpServerTool,
    DisableSkillTool,
    EnableMcpServerTool,
    EnableSkillTool,
    GetMcpServerConfigTool,
    InstallSkillTool,
    ListMcpServersTool,
    ListSkillFilesTool,
    ListSkillsTool,
    ReadSkillFileTool,
    RemoveMcpServerTool,
    UpdateMcpServerTool,
    UpdateSkillFileTool,
    UpdateSkillFromZipTool,
)

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class Main(star.Star):
    """Skills & MCP Manager Plugin"""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config

        # Read diff mode settings
        diff_mode = bool(config.get("diff_mode", False))
        diff_threshold = int(config.get("diff_match_threshold", 100))
        # Clamp to valid range [50, 100]
        diff_threshold = max(50, min(100, diff_threshold))

        # Install builtin skill
        self._install_builtin_skill()

        # Register LLM tools
        context.add_llm_tools(
            # Skills tools
            ListSkillsTool(),
            EnableSkillTool(),
            DisableSkillTool(),
            DeleteSkillTool(),
            InstallSkillTool(),
            ListSkillFilesTool(),
            ReadSkillFileTool(),
            UpdateSkillFileTool(
                diff_mode=diff_mode,
                diff_match_threshold=diff_threshold,
            ),
            UpdateSkillFromZipTool(),
            # MCP tools
            ListMcpServersTool(),
            GetMcpServerConfigTool(),
            EnableMcpServerTool(),
            DisableMcpServerTool(),
            AddMcpServerTool(),
            UpdateMcpServerTool(
                diff_mode=diff_mode,
                diff_match_threshold=diff_threshold,
            ),
            RemoveMcpServerTool(),
        )

    def _install_builtin_skill(self) -> None:
        """Install builtin SKILL.md to AstrBot skills directory."""
        plugin_dir = Path(__file__).parent
        builtin_skill_src = plugin_dir / "skills" / "skills-mcp-manager"
        try:
            mgr = SkillManager()
            skills_root = Path(mgr.skills_root)
            builtin_skill_dest = skills_root / "skills-mcp-manager"

            if not builtin_skill_src.exists():
                return

            if not builtin_skill_dest.exists():
                shutil.copytree(builtin_skill_src, builtin_skill_dest)
                logger.info("已安装内置 Skill: skills-mcp-manager")
            else:
                # Check if update is needed
                src_md = builtin_skill_src / "SKILL.md"
                dest_md = builtin_skill_dest / "SKILL.md"
                if (
                    src_md.exists()
                    and dest_md.exists()
                    and src_md.stat().st_mtime > dest_md.stat().st_mtime
                ):
                    shutil.copytree(
                        builtin_skill_src, builtin_skill_dest, dirs_exist_ok=True
                    )
                    logger.info("已更新内置 Skill: skills-mcp-manager")
        except Exception as e:
            logger.warning(f"安装内置 Skill 失败: {e}")

    # ==================== Utility methods ====================

    @staticmethod
    def _format_file_size(size: int) -> str:
        """Format file size for display."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    # ==================== Skill Command Group ====================

    @filter.command_group("skill")
    def skill_group(self) -> None:
        """Skill 管理命令组"""

    @skill_group.command("ls")
    async def skill_ls(self, event: AstrMessageEvent) -> None:
        """列出所有 Skills"""
        mgr = SkillManager()
        skills = mgr.list_skills()

        lines = ["Skills 列表:\n"]
        for s in skills:
            status = "[运行中]" if s.active else "[已禁用]"
            lines.append(f"  {status} {s.name}: {s.description or '无描述'}")

        if not skills:
            lines.append("  暂无 Skills")

        event.set_result(MessageEventResult().message("\n".join(lines)).use_t2i(False))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("on")
    async def skill_on(self, event: AstrMessageEvent, name: str = "") -> None:
        """启用 Skill"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /skill on <名称>"))
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            mgr = SkillManager()
            mgr.set_skill_active(name, True)
            event.set_result(
                MessageEventResult().message(
                    f"[成功] 已启用 Skill: {name}\n提示: 下一次对话生效"
                )
            )
        except Exception as e:
            logger.error(f"skill_on failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 启用失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("off")
    async def skill_off(self, event: AstrMessageEvent, name: str = "") -> None:
        """禁用 Skill"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /skill off <名称>"))
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            mgr = SkillManager()
            mgr.set_skill_active(name, False)
            event.set_result(
                MessageEventResult().message(
                    f"[成功] 已禁用 Skill: {name}\n提示: 下一次对话生效"
                )
            )
        except Exception as e:
            logger.error(f"skill_off failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 禁用失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("del")
    async def skill_del(self, event: AstrMessageEvent, name: str = "") -> None:
        """删除 Skill"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /skill del <名称>"))
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            mgr = SkillManager()
            mgr.delete_skill(name)
            event.set_result(
                MessageEventResult().message(
                    f"[成功] 已删除 Skill: {name}\n提示: 下一次对话生效"
                )
            )
        except Exception as e:
            logger.error(f"skill_del failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 删除失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("files")
    async def skill_files(self, event: AstrMessageEvent, name: str = "") -> None:
        """列出 Skill 文件结构"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /skill files <名称>"))
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        mgr = SkillManager()
        skills_root = Path(mgr.skills_root)
        skill_dir = (skills_root / name).resolve()

        if not skill_dir.exists():
            event.set_result(
                MessageEventResult().message(f"[失败] Skill 不存在: {name}")
            )
            return

        # Security check: ensure skill_dir is within skills_root
        try:
            skill_dir.relative_to(skills_root.resolve())
        except ValueError:
            event.set_result(MessageEventResult().message("[失败] 非法路径"))
            return

        lines = [f"Skill {name} 文件结构:\n"]
        for root, dirs, files in os.walk(skill_dir):
            rel_root = Path(root).relative_to(skill_dir)
            level = len(rel_root.parts)
            indent = "  " * level

            for d in sorted(dirs):
                lines.append(f"{indent}{d}/")
            for f in sorted(files):
                file_path = Path(root) / f
                size = file_path.stat().st_size
                size_str = self._format_file_size(size)
                lines.append(f"{indent}{f} ({size_str})")

        event.set_result(MessageEventResult().message("\n".join(lines)).use_t2i(False))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("read")
    async def skill_read(
        self, event: AstrMessageEvent, name: str = "", file: str = ""
    ) -> None:
        """读取 Skill 文件内容"""
        if not name or not file:
            event.set_result(
                MessageEventResult().message("用法: /skill read <名称> <文件路径>")
            )
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        mgr = SkillManager()
        skills_root = Path(mgr.skills_root)
        skill_dir = (skills_root / name).resolve()
        file_path = (skill_dir / file).resolve()

        # Security check: constrain to the specific skill directory
        try:
            file_path.relative_to(skill_dir)
        except ValueError:
            event.set_result(MessageEventResult().message("[失败] 非法文件路径"))
            return

        if not file_path.exists():
            event.set_result(MessageEventResult().message(f"[失败] 文件不存在: {file}"))
            return

        try:
            content = file_path.read_text(encoding="utf-8")
            if len(content) > 5000:
                content = content[:5000] + "\n\n... (内容过长，已截断)"
            event.set_result(
                MessageEventResult()
                .message(f"{file}:\n\n```\n{content}\n```")
                .use_t2i(False)
            )
        except Exception as e:
            logger.error(f"skill_read failed: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 读取失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("install")
    async def skill_install(self, event: AstrMessageEvent) -> None:
        """批量安装 Skill（支持多个 ZIP 文件）"""
        import astrbot.api.message_components as Comp

        event.set_result(
            MessageEventResult().message(
                "Skill 批量安装模式已启动\n"
                "发送 ZIP 文件安装 Skill，发送「结束」或「done」完成安装\n"
                "超时时间: 120 秒"
            )
        )

        installed: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []

        @session_waiter(timeout=120, record_history_chains=False)
        async def file_waiter(
            controller: SessionController, event: AstrMessageEvent
        ) -> None:
            nonlocal installed, failed
            user_input = event.message_str.strip().lower()

            if user_input in ("结束", "done", "end", "exit", "quit"):
                if installed or failed:
                    result = _format_install_result(installed, failed)
                    await event.send(event.plain_result(result))
                else:
                    await event.send(
                        event.plain_result("批量安装已结束，未安装任何 Skill")
                    )
                controller.stop()
                return

            has_files = False
            for msg in event.get_messages():
                if isinstance(msg, Comp.File):
                    has_files = True
                    file_name = msg.name
                    if not file_name.lower().endswith(".zip"):
                        await event.send(
                            event.plain_result(
                                f"[警告] {file_name}: 请发送 ZIP 格式的文件"
                            )
                        )
                        continue

                    file_path_str = await msg.get_file()
                    try:
                        mgr = SkillManager()
                        result = mgr.install_skill_from_zip(file_path_str)
                        installed.append((file_name, result))
                        await event.send(
                            event.plain_result(f"[成功] {file_name}: 安装成功")
                        )
                    except Exception as e:
                        failed.append((file_name, str(e)))
                        logger.error(f"skill_install failed for {file_name}: {e}")
                        await event.send(
                            event.plain_result(
                                f"[失败] {file_name}: 安装失败，请查看日志"
                            )
                        )
                    finally:
                        if file_path_str and os.path.exists(file_path_str):
                            os.remove(file_path_str)

            if has_files:
                controller.keep(timeout=120, reset_timeout=True)
                return

            if event.message_str.strip():
                await event.send(
                    event.plain_result("[警告] 请发送 ZIP 文件，或发送「结束」完成安装")
                )
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await file_waiter(event)
        except TimeoutError:
            if installed or failed:
                result = _format_install_result(installed, failed)
                result = "超时自动结束\n\n" + result
                event.set_result(MessageEventResult().message(result).use_t2i(False))
            else:
                event.set_result(MessageEventResult().message("超时，未收到任何文件"))
        finally:
            event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("update")
    async def skill_update(self, event: AstrMessageEvent, name: str = "") -> None:
        """更新 Skill 文件（交互式）"""
        import astrbot.api.message_components as Comp

        if not name:
            event.set_result(MessageEventResult().message("用法: /skill update <名称>"))
            return
        if not _SKILL_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        mgr = SkillManager()
        skills_root = Path(mgr.skills_root)
        skill_dir = (skills_root / name).resolve()

        if not skill_dir.exists():
            event.set_result(
                MessageEventResult().message(
                    f"[失败] Skill 不存在: {name}\n使用 /skill install 安装新 Skill"
                )
            )
            return

        event.set_result(
            MessageEventResult().message(
                f"Skill 更新模式: {name}\n"
                "发送 ZIP 文件覆盖整个 Skill，或发送单个文件更新指定文件\n"
                "发送「结束」或「done」完成更新\n"
                "超时时间: 120 秒"
            )
        )

        updated_files: list[tuple[str, str]] = []
        errors: list[tuple[str, str]] = []

        @session_waiter(timeout=120, record_history_chains=False)
        async def file_waiter(
            controller: SessionController, event: AstrMessageEvent
        ) -> None:
            nonlocal updated_files, errors
            user_input = event.message_str.strip().lower()

            if user_input in ("结束", "done", "end", "exit", "quit"):
                if updated_files or errors:
                    result = _format_update_result(updated_files, errors)
                    await event.send(event.plain_result(result))
                else:
                    await event.send(
                        event.plain_result("更新模式已结束，未更新任何文件")
                    )
                controller.stop()
                return

            has_files = False
            for msg in event.get_messages():
                if isinstance(msg, Comp.File):
                    has_files = True
                    file_name = msg.name
                    file_path_str = await msg.get_file()
                    try:
                        if file_name.lower().endswith(".zip"):
                            count = _validate_and_update_from_zip(
                                skill_dir, file_path_str, name
                            )
                            updated_files.append(
                                (file_name, f"整个 Skill 已更新 ({count} 文件)")
                            )
                            await event.send(
                                event.plain_result(
                                    f"[成功] {file_name}: Skill 已从 ZIP 更新"
                                )
                            )
                        else:
                            dest_path = (skill_dir / file_name).resolve()
                            # Security check: ensure path stays within skill dir
                            try:
                                dest_path.relative_to(skill_dir)
                            except ValueError:
                                errors.append((file_name, "非法文件名: 路径逃逸"))
                                await event.send(
                                    event.plain_result(
                                        f"[失败] {file_name}: 非法文件名"
                                    )
                                )
                                continue
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy(file_path_str, dest_path)
                            updated_files.append((file_name, "已更新"))
                            await event.send(
                                event.plain_result(f"[成功] {file_name}: 已更新")
                            )
                    except Exception as e:
                        errors.append((file_name, str(e)))
                        logger.error(f"skill_update failed for {file_name}: {e}")
                        await event.send(
                            event.plain_result(
                                f"[失败] {file_name}: 更新失败，请查看日志"
                            )
                        )
                    finally:
                        if file_path_str and os.path.exists(file_path_str):
                            os.remove(file_path_str)

            if has_files:
                controller.keep(timeout=120, reset_timeout=True)
                return

            if event.message_str.strip():
                await event.send(
                    event.plain_result(
                        "[警告] 请发送 ZIP 文件或单个文件，或发送「结束」完成更新"
                    )
                )
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await file_waiter(event)
        except TimeoutError:
            if updated_files or errors:
                result = _format_update_result(updated_files, errors)
                result = "超时自动结束\n\n" + result
                event.set_result(MessageEventResult().message(result).use_t2i(False))
            else:
                event.set_result(MessageEventResult().message("超时，未收到任何文件"))
        finally:
            event.stop_event()

    # ==================== MCP Command Group ====================

    @filter.command_group("mcp")
    def mcp_group(self) -> None:
        """MCP 服务器管理命令组"""

    @mcp_group.command("ls")
    async def mcp_ls(self, event: AstrMessageEvent) -> None:
        """列出所有 MCP 服务器"""
        tool_mgr = self.context.get_llm_tool_manager()
        config = tool_mgr.load_mcp_config()
        runtime = tool_mgr.mcp_server_runtime_view

        lines = ["MCP 服务器列表:\n"]
        for name, cfg in config.get("mcpServers", {}).items():
            if not isinstance(cfg, dict):
                continue
            active = cfg.get("active", False)
            if active and name in runtime:
                status = "[运行中]"
            elif active:
                status = "[已启用]"
            else:
                status = "[已禁用]"
            lines.append(f"  {status} {name}")

        if not config.get("mcpServers"):
            lines.append("  暂无 MCP 服务器")

        event.set_result(MessageEventResult().message("\n".join(lines)).use_t2i(False))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("on")
    async def mcp_on(self, event: AstrMessageEvent, name: str = "") -> None:
        """启用 MCP 服务器"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /mcp on <名称>"))
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            config = tool_mgr.load_mcp_config()
            servers = config.get("mcpServers", {})
            if name not in servers:
                event.set_result(
                    MessageEventResult().message(f"[失败] MCP 服务器不存在: {name}")
                )
                return

            server_config = servers[name]

            # Connect first, then persist on success
            await tool_mgr.enable_mcp_server(name, server_config, timeout=30)

            server_config["active"] = True
            config["mcpServers"][name] = server_config
            if not tool_mgr.save_mcp_config(config):
                event.set_result(
                    MessageEventResult().message(
                        f"[警告] 已启用 MCP: {name}，但保存配置失败，重启后需要手动执行 /mcp on {name}"
                    )
                )
                return

            event.set_result(
                MessageEventResult().message(
                    f"[成功] 已启用 MCP: {name}\n提示: 下一次对话生效"
                )
            )
        except TimeoutError:
            event.set_result(
                MessageEventResult().message(f"[失败] 启用 MCP 服务器 {name} 超时")
            )
        except Exception as e:
            logger.error(f"mcp_on failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 启用失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("off")
    async def mcp_off(self, event: AstrMessageEvent, name: str = "") -> None:
        """禁用 MCP 服务器"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /mcp off <名称>"))
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            config = tool_mgr.load_mcp_config()
            servers = config.get("mcpServers", {})
            if name not in servers:
                event.set_result(
                    MessageEventResult().message(f"[失败] MCP 服务器不存在: {name}")
                )
                return

            # Stop runtime first, then persist on success
            if name in tool_mgr.mcp_server_runtime_view:
                await tool_mgr.disable_mcp_server(name, timeout=10)

            servers[name]["active"] = False
            if not tool_mgr.save_mcp_config(config):
                event.set_result(
                    MessageEventResult().message(
                        f"[警告] 已禁用 MCP: {name}，但保存配置失败，重启后需要手动执行 /mcp off {name}"
                    )
                )
                return

            event.set_result(
                MessageEventResult().message(
                    f"[成功] 已禁用 MCP: {name}\n提示: 下一次对话生效"
                )
            )
        except TimeoutError:
            event.set_result(
                MessageEventResult().message(f"[失败] 禁用 MCP 服务器 {name} 超时")
            )
        except Exception as e:
            logger.error(f"mcp_off failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 禁用失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("del")
    async def mcp_del(self, event: AstrMessageEvent, name: str = "") -> None:
        """删除 MCP 服务器"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /mcp del <名称>"))
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            config = tool_mgr.load_mcp_config()

            if name not in config.get("mcpServers", {}):
                event.set_result(
                    MessageEventResult().message(f"[失败] MCP 服务器不存在: {name}")
                )
                return

            # Disable first if running
            if name in tool_mgr.mcp_server_runtime_view:
                await tool_mgr.disable_mcp_server(name, timeout=10)

            del config["mcpServers"][name]
            if not tool_mgr.save_mcp_config(config):
                event.set_result(
                    MessageEventResult().message(
                        f"[警告] 已从运行时移除 MCP: {name}，但保存配置失败"
                    )
                )
                return

            event.set_result(MessageEventResult().message(f"[成功] 已删除 MCP: {name}"))
        except Exception as e:
            logger.error(f"mcp_del failed for {name}: {e}")
            event.set_result(
                MessageEventResult().message("[失败] 删除失败，请查看日志")
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("config")
    async def mcp_config(self, event: AstrMessageEvent, name: str = "") -> None:
        """查看 MCP 服务器详细配置"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /mcp config <名称>"))
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        tool_mgr = self.context.get_llm_tool_manager()
        config = tool_mgr.load_mcp_config()
        servers = config.get("mcpServers", {})

        if name not in servers:
            event.set_result(
                MessageEventResult().message(f"[失败] MCP 服务器不存在: {name}")
            )
            return

        server_config = servers[name]
        runtime = tool_mgr.mcp_server_runtime_view
        active = server_config.get("active", False)
        is_running = name in runtime

        lines = [f"MCP 服务器配置: {name}\n"]

        if is_running:
            status = "[运行中]"
        elif active:
            status = "[已启用]"
        else:
            status = "[已禁用]"
        lines.append(f"状态: {status}\n")

        # Config details
        lines.append("配置:")
        if "command" in server_config:
            lines.append("  类型: stdio")
            lines.append(f"  命令: {server_config['command']}")
            if "args" in server_config:
                lines.append(f"  参数: {' '.join(server_config['args'])}")
            if "env" in server_config:
                env_keys = list(server_config["env"].keys())
                lines.append(f"  环境变量: {', '.join(env_keys)} (已隐藏值)")
        elif "url" in server_config:
            transport = server_config.get("transport", "sse")
            lines.append(f"  类型: {transport}")
            lines.append(f"  URL: {server_config['url']}")
            if "headers" in server_config:
                header_keys = list(server_config["headers"].keys())
                lines.append(f"  Headers: {', '.join(header_keys)} (已隐藏值)")

        # Running tools
        if is_running:
            rt = runtime[name]
            tools = [t.name for t in rt.client.tools]
            if tools:
                lines.append(f"\n可用工具 ({len(tools)} 个):")
                for t in tools[:10]:
                    lines.append(f"  • {t}")
                if len(tools) > 10:
                    lines.append(f"  ... 还有 {len(tools) - 10} 个工具")

        # Masked full config
        masked = mask_sensitive(server_config)
        config_display = json.dumps(masked, ensure_ascii=False, indent=2)
        lines.append("\n完整配置 (已隐藏敏感信息):")
        lines.append(f"```json\n{config_display}\n```")

        event.set_result(MessageEventResult().message("\n".join(lines)).use_t2i(False))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("add")
    async def mcp_add(self, event: AstrMessageEvent, name: str = "") -> None:
        """交互式添加 MCP 服务器"""
        if not name:
            event.set_result(
                MessageEventResult().message("用法: /mcp add <服务器名称>")
            )
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        help_text = (
            "请发送 MCP 服务器配置（JSON 格式）:\n\n"
            '示例 (stdio):\n{"command": "uv", "args": ["tool", "run", "mcp-server"]}\n\n'
            '示例 (SSE):\n{"url": "https://example.com/mcp/sse", "transport": "sse"}\n\n'
            '示例 (HTTP):\n{"url": "https://example.com/mcp", "transport": "streamable_http"}\n\n'
            "等待输入（60秒）..."
        )
        event.set_result(MessageEventResult().message(help_text))

        @session_waiter(timeout=60)
        async def config_waiter(
            controller: SessionController, event: AstrMessageEvent
        ) -> None:
            config_text = event.message_str.strip()

            if config_text.lower() in ("取消", "cancel", "exit", "quit"):
                await event.send(event.plain_result("已取消"))
                controller.stop()
                return

            try:
                server_config = json.loads(config_text)
            except json.JSONDecodeError:
                await event.send(event.plain_result("[失败] JSON 格式错误，请重新发送"))
                controller.keep(timeout=60, reset_timeout=True)
                return

            tool_mgr = self.context.get_llm_tool_manager()

            # Test connection
            try:
                await event.send(event.plain_result("正在测试连接..."))
                await tool_mgr.test_mcp_server_connection(server_config)
            except Exception as e:
                logger.error(f"mcp_add connection test failed: {e}")
                await event.send(
                    event.plain_result("[失败] 连接测试失败，请检查配置或查看日志")
                )
                controller.stop()
                return

            # Save and enable
            server_config["active"] = True
            config = tool_mgr.load_mcp_config()
            config.setdefault("mcpServers", {})[name] = server_config
            if not tool_mgr.save_mcp_config(config):
                await event.send(event.plain_result("[失败] 保存配置失败"))
                controller.stop()
                return

            try:
                await tool_mgr.enable_mcp_server(name, server_config, timeout=30)
                await event.send(
                    event.plain_result(
                        f"[成功] MCP 服务器 '{name}' 添加成功！\n"
                        "提示: 新工具将在下一次对话生效"
                    )
                )
            except Exception as e:
                # Rollback: remove the saved config entry
                logger.error(f"mcp_add: enable failed for {name}: {e}")
                try:
                    rollback_config = tool_mgr.load_mcp_config()
                    rollback_config.get("mcpServers", {}).pop(name, None)
                    if not tool_mgr.save_mcp_config(rollback_config):
                        logger.error(f"mcp_add: rollback save also failed for {name}")
                except Exception:
                    pass
                await event.send(
                    event.plain_result("[失败] 启用失败，已回滚配置，请查看日志")
                )

            controller.stop()

        try:
            await config_waiter(event)
        except TimeoutError:
            event.set_result(MessageEventResult().message("操作超时，请重新发送命令"))
        finally:
            event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mcp_group.command("update")
    async def mcp_update(self, event: AstrMessageEvent, name: str = "") -> None:
        """交互式更新 MCP 服务器配置"""
        if not name:
            event.set_result(MessageEventResult().message("用法: /mcp update <名称>"))
            return
        if not _MCP_NAME_RE.fullmatch(name):
            event.set_result(MessageEventResult().message(f"[失败] 无效名称: {name}"))
            return

        tool_mgr = self.context.get_llm_tool_manager()
        config = tool_mgr.load_mcp_config()
        servers = config.get("mcpServers", {})

        if name not in servers:
            event.set_result(
                MessageEventResult().message(f"[失败] MCP 服务器不存在: {name}")
            )
            return

        current_config = servers[name]
        masked = mask_sensitive(current_config.copy())
        current_json = json.dumps(masked, ensure_ascii=False, indent=2)

        help_text = (
            f"更新 MCP 服务器: {name}\n\n"
            f"当前配置 (敏感信息已隐藏):\n```json\n{current_json}\n```\n\n"
            "请发送新的配置（JSON 格式），或发送「取消」放弃更新:\n"
            "等待输入（60秒）..."
        )
        event.set_result(MessageEventResult().message(help_text))

        @session_waiter(timeout=60)
        async def config_waiter(
            controller: SessionController, event: AstrMessageEvent
        ) -> None:
            config_text = event.message_str.strip()

            if config_text.lower() in ("取消", "cancel", "exit", "quit"):
                await event.send(event.plain_result("已取消更新"))
                controller.stop()
                return

            try:
                new_config = json.loads(config_text)
            except json.JSONDecodeError:
                await event.send(event.plain_result("[失败] JSON 格式错误，请重新发送"))
                controller.keep(timeout=60, reset_timeout=True)
                return

            # Preserve active if not specified
            if "active" not in new_config:
                new_config["active"] = current_config.get("active", True)

            # Test connection
            try:
                await event.send(event.plain_result("正在测试新配置..."))
                await tool_mgr.test_mcp_server_connection(new_config)
            except Exception as e:
                logger.error(f"mcp_update connection test failed: {e}")
                await event.send(
                    event.plain_result("[失败] 连接测试失败，请检查配置或查看日志")
                )
                controller.stop()
                return

            # Disable old if running
            was_active = current_config.get("active", True)
            was_running = name in tool_mgr.mcp_server_runtime_view
            if was_active:
                try:
                    await tool_mgr.disable_mcp_server(name)
                except Exception:
                    pass

            # Save new config
            config["mcpServers"][name] = new_config
            if not tool_mgr.save_mcp_config(config):
                # Rollback: restore old config
                config["mcpServers"][name] = current_config
                if not tool_mgr.save_mcp_config(config):
                    logger.error(f"mcp_update: rollback save also failed for {name}")
                    await event.send(
                        event.plain_result(
                            "[失败] 保存配置失败，且回滚也未成功，请手动检查配置文件"
                        )
                    )
                    controller.stop()
                    return
                if was_running:
                    try:
                        await tool_mgr.enable_mcp_server(
                            name, current_config, timeout=30
                        )
                    except Exception:
                        pass
                await event.send(event.plain_result("[失败] 保存配置失败，已回滚"))
                controller.stop()
                return

            # Re-enable if active
            if new_config.get("active", True):
                try:
                    await tool_mgr.enable_mcp_server(name, new_config, timeout=30)
                except Exception as e:
                    # Rollback: restore old config and re-enable
                    logger.error(f"mcp_update: enable failed for {name}: {e}")
                    config["mcpServers"][name] = current_config
                    if not tool_mgr.save_mcp_config(config):
                        logger.error(f"mcp_update: rollback save failed for {name}")
                        await event.send(
                            event.plain_result(
                                "[失败] 启用新配置失败，且回滚也未成功，请手动检查配置文件"
                            )
                        )
                        controller.stop()
                        return
                    if was_running:
                        try:
                            await tool_mgr.enable_mcp_server(
                                name, current_config, timeout=30
                            )
                        except Exception:
                            pass
                    await event.send(
                        event.plain_result(
                            "[失败] 启用新配置失败，已回滚旧配置，请查看日志"
                        )
                    )
                    controller.stop()
                    return

            await event.send(
                event.plain_result(
                    f"[成功] MCP 服务器 '{name}' 更新成功！\n"
                    "提示: 变更将在下一次对话生效"
                )
            )
            controller.stop()

        try:
            await config_waiter(event)
        except TimeoutError:
            event.set_result(MessageEventResult().message("操作超时，请重新发送命令"))
        finally:
            event.stop_event()


# ==================== Helper functions ====================


def _validate_and_update_from_zip(
    skill_dir: Path, zip_path: str, expected_name: str
) -> int:
    """Validate ZIP and update Skill with best-effort backup and rollback, return number of files updated."""
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = [
            n.replace("\\", "/") for n in zf.namelist() if n and not n.endswith("/")
        ]
        if not names:
            raise ValueError("ZIP 文件为空")

        top_dirs = {Path(n).parts[0] for n in names if n.strip()}
        if len(top_dirs) != 1:
            raise ValueError("ZIP 必须包含单个顶层文件夹")

        zip_skill_name = next(iter(top_dirs))
        if zip_skill_name != expected_name:
            raise ValueError(
                f"ZIP 内文件夹名 '{zip_skill_name}' 与 Skill 名称 '{expected_name}' 不匹配"
            )

        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()

            # Zip Slip protection: validate all member paths before extraction
            for member in zf.namelist():
                member_target = (tmp_path / member).resolve()
                if (
                    not str(member_target).startswith(str(tmp_path) + os.sep)
                    and member_target != tmp_path
                ):
                    raise ValueError(f"ZIP 包含非法路径: {member}")

            zf.extractall(tmp_dir)
            src_dir = Path(tmp_dir) / zip_skill_name

            # Best-effort replacement: move current to backup, then replace
            import uuid as _uuid

            backup_dir = skill_dir.parent / f".{skill_dir.name}.bak.{_uuid.uuid4().hex[:8]}"
            rollback_failed = False
            try:
                # Move current to backup (fast rename, unique path avoids
                # discarding a prior backup from a failed rollback)
                shutil.move(skill_dir, backup_dir)
                skill_dir.mkdir()

                # Copy new files
                for item in src_dir.iterdir():
                    if item.is_dir():
                        shutil.copytree(item, skill_dir / item.name)
                    else:
                        shutil.copy2(item, skill_dir / item.name)

                # Count actual files (recursive)
                file_count = sum(1 for f in src_dir.rglob("*") if f.is_file())
            except Exception:
                # Rollback: restore from backup
                try:
                    if backup_dir.exists():
                        if skill_dir.exists():
                            shutil.rmtree(skill_dir)
                        shutil.move(backup_dir, skill_dir)
                except Exception as rollback_err:
                    rollback_failed = True
                    logger.error(f"Skill 更新回滚失败: {rollback_err}")
                raise
            finally:
                # Clean up backup only if rollback succeeded
                if not rollback_failed and backup_dir.exists():
                    try:
                        shutil.rmtree(backup_dir)
                    except Exception:
                        pass

            return file_count


def _format_install_result(
    installed: list[tuple[str, str]], failed: list[tuple[str, str]]
) -> str:
    """Format skill installation result."""
    lines = ["Skill 安装结果:\n"]
    if installed:
        lines.append("[成功] 成功:")
        for n, r in installed:
            lines.append(f"  • {n}: {r}")
    if failed:
        lines.append("\n[失败] 失败:")
        for n, e in failed:
            lines.append(f"  • {n}: {e}")
    lines.append("\n提示: 新 Skills 将在下次对话生效")
    return "\n".join(lines)


def _format_update_result(
    updated: list[tuple[str, str]], errors: list[tuple[str, str]]
) -> str:
    """Format skill update result."""
    lines = ["Skill 更新结果:\n"]
    if updated:
        lines.append("[成功] 更新成功:")
        for n, info in updated:
            lines.append(f"  • {n}: {info}")
    if errors:
        lines.append("\n[失败] 更新失败:")
        for n, error in errors:
            lines.append(f"  • {n}: {error}")
    lines.append("\n提示: Skill 变更将在下次对话生效")
    return "\n".join(lines)
