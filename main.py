"""Skills & MCP Manager Plugin - main entry point.

Provides LLM tool interfaces and user commands for managing AstrBot Skills and MCP servers.
"""

from __future__ import annotations

import json
import os
import re
import time
import shlex
import tempfile
import zipfile
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.core.skills.skill_manager import SkillManager
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionFilter,
    session_waiter,
)

from .services.scan_delivery import ScanDelivery
from .services.scan_review import ScanReview
from .services.review_language import confirmation_word
from .services.skill_install import SkillInstallService, failed_result
from .services.skill_sources import SkillSources, SourceError
from .tools.skill_tools import _try_sync_to_sandboxes
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
    ListSkillsTool,
    RemoveMcpServerTool,
    UpdateMcpServerTool,
    UpdateSkillFromZipTool,
)

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class _SkillSessionFilter(SessionFilter):
    def filter(self, event: AstrMessageEvent) -> str:
        return json.dumps(
            ["skills_mcp_install", event.unified_msg_origin, event.get_sender_id()]
        )


def _skill_link_arguments(*args):
    values = [value for value in args if value and value != "--force"]
    if len(values) > 2 or (values and not values[0].startswith("https://")):
        raise ValueError("参数无效")
    return (
        values[0] if values else "",
        values[1] if len(values) == 2 else "",
        "--force" in args,
    )


class Main(star.Star):
    """Skills & MCP Manager Plugin"""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self.skill_installer = SkillInstallService(
            config.get("skill_scan_mode", "enforce")
        )
        self.skill_sources = SkillSources(
            config.get("skill_github_token", ""), config.get("skillhub_api_key", "")
        )
        self._active_skill_sessions = set()
        review_language = config.get("skill_review_language", "简体中文")
        self.scan_delivery = ScanDelivery(self, language=review_language)
        self.scan_review = ScanReview(
            context,
            config.get("skill_review_provider_id", ""),
            language=review_language,
        )
        self.skill_confirm_timeout = max(
            1, int(config.get("skill_confirm_timeout", 300))
        )

        # Read diff mode settings
        diff_mode = bool(config.get("diff_mode", False))
        diff_threshold = int(config.get("diff_match_threshold", 100))
        # Clamp to valid range [50, 100]
        diff_threshold = max(50, min(100, diff_threshold))

        # Register LLM tools
        context.add_llm_tools(
            # Skills tools
            ListSkillsTool(),
            EnableSkillTool(),
            DisableSkillTool(),
            DeleteSkillTool(),
            InstallSkillTool(installer=self.skill_installer, reviewer=self.scan_review),
            UpdateSkillFromZipTool(
                installer=self.skill_installer, reviewer=self.scan_review
            ),
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

    @filter.on_llm_request()
    async def inject_skill_scan_reports(self, event: AstrMessageEvent, req) -> None:
        await self.scan_delivery.inject_pending(event, req)

    @filter.on_llm_response()
    async def acknowledge_skill_scan_reports(
        self, event: AstrMessageEvent, resp
    ) -> None:
        await self.scan_delivery.acknowledge_response(event, resp)

    async def _prepare_skill_upload(
        self,
        event: AstrMessageEvent,
        attachment,
        *,
        operation: str = "install",
        skill_name: str = "",
        force: bool = False,
    ):
        file_path = None
        prepared = None
        try:
            if event.role != "admin":
                result = failed_result(
                    "只有管理员可以安装或更新 Skill。", code="permission_denied"
                )
            else:
                file_path = await attachment.get_file()
                result, prepared = await self.skill_installer.prepare(
                    SkillManager(),
                    file_path,
                    operation=operation,
                    skill_name=skill_name,
                    file_name=attachment.name,
                    archive_name=attachment.name,
                    force=force,
                )
        except Exception:
            logger.exception("Skill upload preparation failed")
            result = failed_result("无法取得上传文件或初始化安装服务。")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    logger.warning("Could not remove temporary skill upload")
        return await self._show_skill_report(event, result), prepared

    async def _prepare_skill_link(
        self, event, url, selection="", *, skill_name="", force=False
    ):
        prepared = None
        try:
            if event.role != "admin":
                result = failed_result(
                    "只有管理员可以安装或更新 Skill。", code="permission_denied"
                )
            else:
                bundle = await self.skill_sources.resolve(url, selection)
                with tempfile.TemporaryDirectory(prefix="skill_link_") as tmp:
                    archive = Path(tmp) / "skill.zip"
                    name = skill_name or bundle.name
                    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
                        for path, content in bundle.files.items():
                            zf.writestr(name + "/" + path, content)
                    result, prepared = await self.skill_installer.prepare(
                        SkillManager(),
                        str(archive),
                        operation="replace" if skill_name else "install",
                        skill_name=skill_name,
                        force=force,
                    )
                result["source"] = bundle.provenance
        except SourceError as exc:
            result = failed_result(str(exc), code="source_unavailable")
        except Exception:
            logger.exception("Skill link preparation failed")
            result = failed_result(
                "链接解析或下载失败，未安装 Skill。", code="source_unavailable"
            )
        return await self._show_skill_report(event, result), prepared

    async def _show_skill_report(self, event, result):
        result = await self.scan_review.review(result)
        if self.scan_review.provider_id:
            review = result["model_review"]
            if review["status"] == "completed":
                await event.send(event.plain_result(review["opinion"]))
            else:
                await event.send(
                    event.plain_result(
                        "审查模型未返回报告，以下是静态检查结果：\n"
                        + json.dumps(result, ensure_ascii=False, indent=2)
                    )
                )
        else:
            await self.scan_delivery.deliver(event, result)
        return result

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
    async def skill_install(
        self,
        event: AstrMessageEvent,
        source: str = "",
        selection: str = "",
        force: str = "",
    ) -> None:
        """上传 Skill，先审查再等待精确确认。"""
        try:
            source, selection, forced = _skill_link_arguments(source, selection, force)
        except ValueError:
            await event.send(
                event.plain_result("用法: /skill install [链接] [技能名] [--force]")
            )
            event.stop_event()
            return
        await self._skill_upload_session(
            event, force=forced, source=source, selection=selection
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @skill_group.command("update")
    async def skill_update(
        self,
        event: AstrMessageEvent,
        name: str = "",
        source: str = "",
        selection: str = "",
        force: str = "",
    ) -> None:
        """上传更新，审查后等待确认；强制模式仍需要确认。"""
        try:
            source, selection, forced = _skill_link_arguments(source, selection, force)
        except ValueError:
            await event.send(
                event.plain_result(
                    "用法: /skill update <名称> [链接] [技能名] [--force]"
                )
            )
            event.stop_event()
            return
        if not name or not _SKILL_NAME_RE.fullmatch(name) or name in {".", ".."}:
            await event.send(event.plain_result("用法: /skill update <名称> [--force]"))
            event.stop_event()
            return
        await self._skill_upload_session(
            event, skill_name=name, force=forced, source=source, selection=selection
        )

    async def _skill_upload_session(
        self,
        origin: AstrMessageEvent,
        *,
        skill_name: str = "",
        force: bool = False,
        source: str = "",
        selection: str = "",
    ) -> None:
        key = (origin.unified_msg_origin, origin.get_sender_id())
        if key in self._active_skill_sessions:
            await origin.send(
                origin.plain_result("已有 Skill 安装或更新任务，请先完成当前任务。")
            )
            origin.stop_event()
            return
        self._active_skill_sessions.add(key)
        try:
            await self._run_skill_upload_session(
                origin,
                skill_name=skill_name,
                force=force,
                source=source,
                selection=selection,
            )
        finally:
            self._active_skill_sessions.discard(key)

    async def _run_skill_upload_session(
        self,
        origin: AstrMessageEvent,
        *,
        skill_name: str = "",
        force: bool = False,
        source: str = "",
        selection: str = "",
    ) -> None:
        import astrbot.api.message_components as Comp

        pending = []
        phase = "upload"
        deadline = 0.0
        confirmed = False
        confirmation_event = origin
        sender = origin.get_sender_id()
        umo = origin.unified_msg_origin
        confirm_word = confirmation_word(self.scan_review.language)

        def discard():
            for candidate in pending:
                candidate.files.clear()
                candidate.consumed = True
            pending.clear()

        async def confirmation_prompt(event):
            names = [p.result["data"]["skill_name"] for p in pending]
            await event.send(
                event.plain_result(
                    "尚未安装/更新。待确认内容："
                    + "；".join(names)
                    + ("\n强制模式：将忽略内容分析结果，但仍需确认。" if force else "")
                    + f"\n请在 {self.skill_confirm_timeout} 秒内仅发送“{confirm_word}”；超时不安装。"
                )
            )

        @session_waiter(
            timeout=self.skill_confirm_timeout if source else 120,
            record_history_chains=False,
        )
        async def waiter(
            controller: SessionController, event: AstrMessageEvent
        ) -> None:
            nonlocal phase, deadline, confirmed, confirmation_event
            if event.get_sender_id() != sender or event.unified_msg_origin != umo:
                return
            if event.role != "admin":
                await event.send(event.plain_result("管理员权限已失效，取消安装。"))
                controller.stop()
                return
            if phase == "confirm":
                if time.monotonic() >= deadline:
                    controller.stop(TimeoutError("确认超时"))
                    return
                # Do not trim, infer intent, accept synonyms, or extend the deadline.
                messages = event.get_messages()
                if (
                    event.message_str != confirm_word
                    or any(not isinstance(msg, Comp.Plain) for msg in messages)
                    or "".join(msg.text for msg in messages) != confirm_word
                ):
                    await event.send(
                        event.plain_result(
                            f"尚未安装。仅接受纯文本“{confirm_word}”，其他回复不会延长确认期限。"
                        )
                    )
                    return
                confirmed = True
                confirmation_event = event
                controller.stop()
                return
            if event.message_str in {"结束", "取消", "done"}:
                controller.stop()
                return
            attachments = [
                msg for msg in event.get_messages() if isinstance(msg, Comp.File)
            ]
            link = event.message_str.strip()
            if not attachments and link.startswith("https://"):
                try:
                    args = shlex.split(link)
                    if len(args) > 2:
                        raise ValueError()
                except ValueError:
                    await event.send(event.plain_result("请发送：链接 [技能名]。"))
                    return
                controller.keep(timeout=180, reset_timeout=True)
                result, candidate = await self._prepare_skill_link(
                    event,
                    args[0],
                    args[1] if len(args) > 1 else "",
                    skill_name=skill_name,
                    force=force,
                )
                if controller.future.done():
                    if candidate is not None:
                        candidate.files.clear()
                        candidate.consumed = True
                    discard()
                    return
                if candidate is not None:
                    pending.append(candidate)
                else:
                    await event.send(
                        event.plain_result("该链接未进入待安装队列：" + result["error"])
                    )
            elif not attachments:
                await event.send(
                    event.plain_result(
                        "请发送技能链接或 ZIP 文件；更新也可发送单个文件。发送“取消”结束。"
                    )
                )
                return
            for attachment in attachments:
                if not skill_name and not attachment.name.lower().endswith(".zip"):
                    await event.send(event.plain_result("安装仅支持 ZIP 文件。"))
                    continue
                controller.keep(timeout=120, reset_timeout=True)
                operation = (
                    ("replace" if attachment.name.lower().endswith(".zip") else "file")
                    if skill_name
                    else "install"
                )
                result, candidate = await self._prepare_skill_upload(
                    event,
                    attachment,
                    operation=operation,
                    skill_name=skill_name,
                    force=force,
                )
                if controller.future.done():
                    if candidate is not None:
                        candidate.files.clear()
                        candidate.consumed = True
                    discard()
                    return
                if candidate is not None:
                    pending.append(candidate)
                else:
                    await event.send(
                        event.plain_result("该文件未进入待安装队列：" + result["error"])
                    )
            if not pending:
                controller.stop()
                return
            await confirmation_prompt(event)
            if controller.future.done():
                discard()
                return
            phase = "confirm"
            deadline = time.monotonic() + self.skill_confirm_timeout
            controller.keep(timeout=self.skill_confirm_timeout, reset_timeout=True)

        try:
            await origin.send(
                origin.plain_result(
                    (
                        f"Skill 更新模式: {skill_name}"
                        if skill_name
                        else "Skill 安装模式"
                    )
                    + (
                        "\n正在解析并下载技能链接。"
                        if source
                        else "\n请在 120 秒内发送技能链接或上传文件。"
                    )
                    + f"将先返回报告，再等待“{confirm_word}”。"
                    + (
                        "\n已启用 --force，内容分析不阻断安装；路径和包结构检查仍执行。"
                        if force
                        else ""
                    )
                )
            )
            if source:
                result, candidate = await self._prepare_skill_link(
                    origin,
                    source,
                    selection,
                    skill_name=skill_name,
                    force=force,
                )
                if candidate is None:
                    await origin.send(origin.plain_result("未安装：" + result["error"]))
                    return
                pending.append(candidate)
                await confirmation_prompt(origin)
                phase = "confirm"
                deadline = time.monotonic() + self.skill_confirm_timeout
            await waiter(origin, session_filter=_SkillSessionFilter())
            if confirmed:
                for candidate in pending:
                    result = await self.skill_installer.commit(candidate)
                    if result["ok"]:
                        _try_sync_to_sandboxes()
                    await confirmation_event.send(
                        confirmation_event.plain_result(
                            result.get("message") if result["ok"] else result["error"]
                        )
                    )
            else:
                await origin.send(
                    origin.plain_result("操作已取消，未安装或更新 Skill。")
                )
        except TimeoutError:
            await origin.send(
                origin.plain_result("等待超时，未安装或更新 Skill。请重新发起命令。")
            )
        finally:
            discard()
            origin.stop_event()

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
                logger.error(f"mcp_on: save_mcp_config failed for {name}")
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
                logger.error(f"mcp_off: save_mcp_config failed for {name}")
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
                logger.error(f"mcp_del: save_mcp_config failed for {name}")
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
                logger.error(f"mcp_add: save_mcp_config failed for {name}")
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
