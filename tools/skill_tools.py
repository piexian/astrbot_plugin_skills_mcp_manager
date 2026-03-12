"""Skills management FunctionTool classes for LLM tool-calling."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.skills.skill_manager import SkillManager, _SKILL_NAME_RE

_REFRESH_HINT = (
    "提示: 本次会话工具集为快照，变更需下一次请求生效；请发送一条新消息刷新。"
)


def _ensure_admin(context: ContextWrapper[AstrAgentContext]) -> str | None:
    """Return an error JSON string if the user is not admin, else None."""
    if context.context.event.role != "admin":
        sender = context.context.event.get_sender_id()
        return json.dumps(
            {
                "ok": False,
                "error": f"权限不足。用户 {sender} 不是管理员。请在管理面板配置管理员。",
            },
            ensure_ascii=False,
        )
    return None


def _validate_skill_name(name: str) -> str | None:
    """Return an error JSON string if skill name is invalid, else None."""
    if not name or not _SKILL_NAME_RE.fullmatch(name):
        return json.dumps(
            {
                "ok": False,
                "error": f"无效的 Skill 名称: '{name}'。只允许字母、数字、点、横线、下划线。",
            },
            ensure_ascii=False,
        )
    return None


def _ok(data: Any = None, message: str = "") -> str:
    result: dict[str, Any] = {"ok": True}
    if data is not None:
        result["data"] = data
    if message:
        result["message"] = message
    return json.dumps(result, ensure_ascii=False)


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False)


def _get_skill_manager() -> SkillManager:
    return SkillManager()


# ---------------------------------------------------------------------------
# ListSkillsTool
# ---------------------------------------------------------------------------


@dataclass
class ListSkillsTool(FunctionTool):
    """List all available Skills."""

    name: str = "list_skills"
    description: str = "列出所有可用的 Skills。返回每个 Skill 的名称、描述、激活状态和来源信息。无需管理员权限。"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        try:
            mgr = _get_skill_manager()
            skills = mgr.list_skills()
            result = [
                {
                    "name": s.name,
                    "description": s.description or "",
                    "active": s.active,
                    "source_type": s.source_type,
                    "local_exists": s.local_exists,
                }
                for s in skills
            ]
            return _ok(data={"skills": result})
        except Exception as e:
            logger.error(f"list_skills failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# EnableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class EnableSkillTool(FunctionTool):
    """Enable a specified Skill."""

    name: str = "enable_skill"
    description: str = "启用指定的 Skill。需要管理员权限。"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要启用的 Skill 名称",
                }
            },
            "required": ["skill_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        try:
            mgr = _get_skill_manager()
            mgr.set_skill_active(skill_name, True)
            return _ok(message=f"已启用 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"enable_skill failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# DisableSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DisableSkillTool(FunctionTool):
    """Disable a specified Skill."""

    name: str = "disable_skill"
    description: str = (
        "禁用指定的 Skill。需要管理员权限。禁用后该 Skill 的指令将不会被加载。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要禁用的 Skill 名称",
                }
            },
            "required": ["skill_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        try:
            mgr = _get_skill_manager()
            mgr.set_skill_active(skill_name, False)
            return _ok(message=f"已禁用 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"disable_skill failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# DeleteSkillTool
# ---------------------------------------------------------------------------


@dataclass
class DeleteSkillTool(FunctionTool):
    """Delete a specified Skill (irreversible)."""

    name: str = "delete_skill"
    description: str = (
        "删除指定的 Skill。需要管理员权限。此操作不可逆，将删除 Skill 的所有文件。"
        "调用前务必确认用户意图。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要删除的 Skill 名称",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认删除，必须为 true 才会执行",
                },
            },
            "required": ["skill_name", "confirm"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if not confirm:
            return _err("请将 confirm 参数设为 true 以确认删除操作。")
        if err := _validate_skill_name(skill_name):
            return err
        try:
            mgr = _get_skill_manager()
            mgr.delete_skill(skill_name)
            return _ok(message=f"已删除 Skill: {skill_name}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"delete_skill failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# InstallSkillTool
# ---------------------------------------------------------------------------


@dataclass
class InstallSkillTool(FunctionTool):
    """Install a Skill from a ZIP file."""

    name: str = "install_skill"
    description: str = (
        "从 ZIP 文件安装 Skill。需要管理员权限。"
        "ZIP 文件路径应为本地绝对路径，必须包含单个顶层文件夹和 SKILL.md 文件。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "zip_path": {
                    "type": "string",
                    "description": "ZIP 文件的本地绝对路径",
                }
            },
            "required": ["zip_path"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        zip_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if not zip_path:
            return _err("zip_path 不能为空。")
        try:
            mgr = _get_skill_manager()
            skill_name = mgr.install_skill_from_zip(zip_path)
            return _ok(
                data={"skill_name": skill_name},
                message=f"Skill 安装成功: {skill_name}。{_REFRESH_HINT}",
            )
        except Exception as e:
            logger.error(f"install_skill failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# ListSkillFilesTool
# ---------------------------------------------------------------------------


@dataclass
class ListSkillFilesTool(FunctionTool):
    """List the file structure of a specified Skill."""

    name: str = "list_skill_files"
    description: str = (
        "列出指定 Skill 的文件结构。无需管理员权限。返回 Skill 目录下的所有文件列表。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称",
                }
            },
            "required": ["skill_name"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _validate_skill_name(skill_name):
            return err
        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            skill_dir = skills_root / skill_name
            if not skill_dir.exists():
                return _err(f"Skill 不存在: {skill_name}")

            files: list[dict[str, Any]] = []
            for root, _dirs, filenames in os.walk(skill_dir):
                for fname in filenames:
                    fpath = Path(root) / fname
                    rel = fpath.relative_to(skill_dir)
                    files.append(
                        {
                            "path": str(rel).replace("\\", "/"),
                            "size": fpath.stat().st_size,
                        }
                    )
            return _ok(data={"skill_name": skill_name, "files": files})
        except Exception as e:
            logger.error(f"list_skill_files failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# ReadSkillFileTool
# ---------------------------------------------------------------------------


@dataclass
class ReadSkillFileTool(FunctionTool):
    """Read the content of a file in a specified Skill."""

    name: str = "read_skill_file"
    description: str = (
        "读取指定 Skill 中的文件内容。无需管理员权限。"
        "可用于读取 SKILL.md 或其他脚本文件。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称",
                },
                "file_path": {
                    "type": "string",
                    "description": "相对文件路径（如 SKILL.md, scripts/run.sh）",
                },
            },
            "required": ["skill_name", "file_path"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        file_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _validate_skill_name(skill_name):
            return err
        if not file_path:
            return _err("file_path 不能为空。")

        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            target = (skills_root / skill_name / file_path).resolve()

            # Security check: ensure path stays within skill directory
            if not str(target).startswith(str(skills_root.resolve())):
                return _err("非法文件路径: 不允许访问 skills 目录外的文件。")
            if not target.exists():
                return _err(f"文件不存在: {file_path}")
            if not target.is_file():
                return _err(f"'{file_path}' 不是文件。")

            content = target.read_text(encoding="utf-8")
            if len(content) > 10000:
                content = content[:10000] + "\n\n... (内容过长，已截断至 10000 字符)"
            return _ok(
                data={
                    "skill_name": skill_name,
                    "file_path": file_path,
                    "content": content,
                }
            )
        except Exception as e:
            logger.error(f"read_skill_file failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# UpdateSkillFileTool
# ---------------------------------------------------------------------------


@dataclass
class UpdateSkillFileTool(FunctionTool):
    """Update the content of a file in a specified Skill."""

    name: str = "update_skill_file"
    description: str = (
        "更新指定 Skill 中的文件内容。需要管理员权限。用于修改 SKILL.md 或脚本文件。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称",
                },
                "file_path": {
                    "type": "string",
                    "description": "相对文件路径（如 SKILL.md, scripts/run.sh）",
                },
                "content": {
                    "type": "string",
                    "description": "文件内容",
                },
            },
            "required": ["skill_name", "file_path", "content"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        file_path: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if err := _validate_skill_name(skill_name):
            return err
        if not file_path:
            return _err("file_path 不能为空。")

        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            target = (skills_root / skill_name / file_path).resolve()

            # Security check
            if not str(target).startswith(str(skills_root.resolve())):
                return _err("非法文件路径: 不允许写入 skills 目录外的文件。")

            skill_dir = skills_root / skill_name
            if not skill_dir.exists():
                return _err(f"Skill 不存在: {skill_name}")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return _ok(message=f"已更新文件: {skill_name}/{file_path}。{_REFRESH_HINT}")
        except Exception as e:
            logger.error(f"update_skill_file failed: {e}")
            return _err(str(e))


# ---------------------------------------------------------------------------
# UpdateSkillFromZipTool
# ---------------------------------------------------------------------------


@dataclass
class UpdateSkillFromZipTool(FunctionTool):
    """Update an existing Skill from a ZIP file."""

    name: str = "update_skill_from_zip"
    description: str = (
        "从 ZIP 文件更新已存在的 Skill。需要管理员权限。会覆盖 Skill 的所有文件。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要更新的 Skill 名称",
                },
                "zip_path": {
                    "type": "string",
                    "description": "ZIP 文件的本地绝对路径",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "确认覆盖更新，必须为 true 才会执行",
                },
            },
            "required": ["skill_name", "zip_path", "confirm"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        skill_name: str = "",
        zip_path: str = "",
        confirm: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        if err := _ensure_admin(context):
            return err
        if not confirm:
            return _err("请将 confirm 参数设为 true 以确认覆盖更新操作。")
        if err := _validate_skill_name(skill_name):
            return err
        if not zip_path:
            return _err("zip_path 不能为空。")

        try:
            mgr = _get_skill_manager()
            skills_root = Path(mgr.skills_root)
            skill_dir = skills_root / skill_name
            if not skill_dir.exists():
                return _err(
                    f"Skill 不存在: {skill_name}。请使用 install_skill 安装新 Skill。"
                )

            # Use install_skill_from_zip with overwrite=True
            installed_name = mgr.install_skill_from_zip(zip_path, overwrite=True)
            return _ok(
                data={"skill_name": installed_name},
                message=f"已从 ZIP 更新 Skill: {installed_name}。{_REFRESH_HINT}",
            )
        except Exception as e:
            logger.error(f"update_skill_from_zip failed: {e}")
            return _err(str(e))
