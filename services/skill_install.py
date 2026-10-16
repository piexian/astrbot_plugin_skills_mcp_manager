from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..security.scanner import (
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    InspectionError,
    ScanReport,
    audit_installed_skills,
    inspect_skill_permission_issue,
    load_directory,
    load_zip,
    member_path,
    read_bounded,
    scan_files,
    valid_skill_name,
)

logger = logging.getLogger(__name__)


def _fingerprint(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


@dataclass
class PreparedInstall:
    manager: Any
    files: dict[str, bytes]
    result: dict
    operation: str
    skill_name: str
    relative: str = ""
    targets: tuple[str, ...] = ()
    previous_hash: str = ""
    consumed: bool = False


def failed_result(message: str, *, code: str = "input_unavailable") -> dict:
    report = ScanReport()
    report.incomplete(code, message)
    return {
        "ok": False,
        "error": message,
        "operation_status": "not_performed",
        "scan": report.to_dict(),
    }


def _target(root: Path, name: str) -> Path:
    if not valid_skill_name(name):
        raise InspectionError("invalid_name", "Skill 名称无效。")
    target = root / name
    if target.is_symlink() or target.resolve().parent != root.resolve():
        raise InspectionError("unsafe_target", "目标 Skill 路径不安全。")
    return target


def _write_tree(files: dict[str, bytes], destination: Path) -> None:
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _make_staging_dir(base: Path, prefix: str = "skill_update_") -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(32):
        candidate = base / f"{prefix}{uuid.uuid4().hex[:12]}"
        try:
            # Avoid mode 0o700 (used by tempfile.mkdtemp): on Windows Python 3.12.4+/3.13+,
            # mode 0o700 applies a protected Admin-only DACL (D:P) that disables inheritance.
            os.mkdir(candidate, 0o755)
            return candidate
        except FileExistsError:
            continue
    raise OSError("无法创建临时交换目录。")


def _reset_windows_acl(target: Path) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["icacls", str(target), "/reset", "/t", "/c", "/q"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        logger.debug("Could not run icacls reset on %s", target, exc_info=True)


def _normalize_permissions(target: Path, root: Path) -> None:
    if not target.exists():
        return
    if os.name == "nt":
        if target.is_dir() and inspect_skill_permission_issue(target, root):
            _reset_windows_acl(target)
        pending = [target]
        while pending:
            current = pending.pop()
            try:
                os.chmod(current, stat.S_IREAD | stat.S_IWRITE)
                if current.is_dir() and not current.is_symlink():
                    with os.scandir(current) as children:
                        for entry in children:
                            pending.append(Path(entry.path))
            except OSError:
                continue
        return

    try:
        root_stat = root.stat()
        root_mode = stat.S_IMODE(root_stat.st_mode)
        chown_ids = (
            (root_stat.st_uid, root_stat.st_gid)
            if hasattr(os, "geteuid") and os.geteuid() == 0 and root_stat.st_uid != 0
            else None
        )
    except OSError:
        root_mode = 0o755
        chown_ids = None

    dir_bits = (root_mode & 0o777) | 0o755
    file_bits = (dir_bits & 0o666) | 0o644
    exec_bits = (dir_bits & 0o111) | 0o111

    pending = [target]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            continue
        cur_mode = stat.S_IMODE(info.st_mode)
        try:
            if stat.S_ISDIR(info.st_mode):
                os.chmod(current, (cur_mode | dir_bits) & 0o777)
                with os.scandir(current) as children:
                    for entry in children:
                        pending.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                extra_exec = exec_bits if (cur_mode & 0o111) else 0
                os.chmod(current, (cur_mode | file_bits | extra_exec) & 0o777)
            if chown_ids is not None:
                os.chown(current, *chown_ids)
        except OSError:
            continue


def _replace_tree(files: dict[str, bytes], target: Path) -> None:
    # Stage beside skills_root, never inside it: partially built skills must not be discoverable.
    tmp = _make_staging_dir(target.parent.parent)
    staged, backup = tmp / "new", tmp / "backup"
    preserve_backup = False
    try:
        _write_tree(files, staged)
        _normalize_permissions(staged, target.parent)
        target.rename(backup)
        try:
            staged.rename(target)
        except Exception:
            try:
                backup.rename(target)
            except Exception:
                preserve_backup = True
                logger.exception("Skill rollback failed; backup retained at %s", backup)
            raise
        _normalize_permissions(target, target.parent)
    finally:
        if not preserve_backup:
            try:
                shutil.rmtree(tmp)
            except OSError:
                logger.warning("Could not clean skill staging directory %s", tmp)


def _replace_file(content: bytes, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            if target.exists():
                temporary.chmod(target.stat().st_mode & 0o777)
            stream.close()
            os.replace(temporary, target)
            _normalize_permissions(target, target.parent)
        finally:
            temporary.unlink(missing_ok=True)


class SkillInstallService:
    def __init__(self, mode: str = "enforce"):
        self.mode = mode if mode in {"enforce", "report_only"} else "enforce"
        self._mutation_lock = asyncio.Lock()

    async def _worker(self, function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def prepare(
        self,
        manager: Any,
        source: str,
        *,
        operation: str = "install",
        skill_name: str = "",
        skill_name_hint: str = "",
        file_name: str = "",
        archive_name: str = "",
        force: bool = False,
    ) -> tuple[dict, PreparedInstall | None]:
        async with self._mutation_lock:
            return await self._worker(
                self._prepare,
                manager,
                source,
                operation,
                skill_name,
                skill_name_hint,
                file_name,
                archive_name,
                force,
                True,
            )

    async def commit(self, prepared: PreparedInstall) -> dict:
        async with self._mutation_lock:
            return await self._worker(self._commit, prepared)

    async def run(
        self,
        manager: Any,
        source: str,
        *,
        operation: str = "install",
        skill_name: str = "",
        skill_name_hint: str = "",
        file_name: str = "",
        archive_name: str = "",
    ) -> dict:
        # LLM tools retain their existing confirmation contract.
        async with self._mutation_lock:
            result, prepared = await self._worker(
                self._prepare,
                manager,
                source,
                operation,
                skill_name,
                skill_name_hint,
                file_name,
                archive_name,
                False,
            )
            if prepared is None:
                return result
            return await self._worker(self._commit, prepared)

    def _prepare(
        self,
        manager,
        source,
        operation,
        skill_name,
        skill_name_hint,
        file_name,
        archive_name,
        force,
        strict=False,
    ):
        report = ScanReport(mode="enforce" if strict else self.mode)
        result = {
            "ok": False,
            "operation": operation,
            "operation_status": "not_performed",
            "force": force,
        }
        try:
            root = Path(manager.skills_root).resolve()
            previous_hash, relative = "", ""
            if operation in {"replace", "file"}:
                target = _target(root, skill_name)
                if not target.is_dir():
                    raise InspectionError(
                        "missing_skill", "目标 Skill 不存在，请先安装。"
                    )
                previous = load_directory(target)
                previous_hash = _fingerprint(previous)
            if operation == "file":
                files = previous.copy()
                relative = member_path(file_name)
                files[relative] = read_bounded(Path(source), MAX_FILE_BYTES)
                if (
                    len(files) > MAX_ENTRIES
                    or sum(map(len, files.values())) > MAX_TOTAL_BYTES
                ):
                    raise InspectionError("resource_limit", "更新后内容超过扫描上限。")
            else:
                files = load_zip(Path(source))

            scan_files(files, report)
            plugins_root = getattr(manager, "plugins_root", None)
            legacy_warnings = audit_installed_skills(
                root,
                report,
                plugins_root=Path(plugins_root).resolve() if plugins_root else None,
            )
            if legacy_warnings:
                result["legacy_permission_warnings"] = legacy_warnings
            # Force can override content analysis, never unsafe paths or unbounded ingestion.
            if report.decision == "block" and not force:
                result["error"] = "扫描未通过，未安装或更新 Skill。"
                return result | {"scan": report.to_dict()}, None
            targets = []
            if operation == "install":
                root_mode = any(n in files for n in ("SKILL.md", "skill.md"))
                hint = re.sub(
                    r"\s+",
                    "_",
                    (skill_name_hint or Path(archive_name or source).stem).strip(),
                )
                if root_mode:
                    if not valid_skill_name(hint):
                        raise ValueError("Skill 名称无效，请指定 skill_name_hint。")
                    files = {f"{hint}/{name}": data for name, data in files.items()}
                elif skill_name_hint:
                    top_dirs = {n.split("/")[0] for n in files}
                    if len(top_dirs) != 1 or not valid_skill_name(hint):
                        raise ValueError("指定名称时 ZIP 必须包含一个 Skill 目录。")
                    prefix = next(iter(top_dirs)) + "/"
                    files = {
                        hint + "/" + n[len(prefix) :]: data for n, data in files.items()
                    }
                targets = sorted(
                    {
                        re.sub(r"\s+", "_", n.split("/")[0].strip())
                        for n in files
                        if n.count("/") == 1
                        and n.split("/")[1] in {"SKILL.md", "skill.md"}
                    }
                )
                raw_targets = {
                    n.split("/")[0]
                    for n in files
                    if n.count("/") == 1 and n.split("/")[1] in {"SKILL.md", "skill.md"}
                }
                if not targets:
                    raise ValueError("ZIP 中没有有效的 Skill 目录及 SKILL.md。")
                if len({n.casefold() for n in targets}) != len(raw_targets):
                    raise ValueError("ZIP 中多个 Skill 名称规范化后发生冲突。")
                for name in targets:
                    if _target(root, name).exists():
                        raise FileExistsError("同名 Skill 已存在，请使用更新操作。")
            elif operation in {"replace", "file"}:
                if operation == "replace":
                    prefix = skill_name + "/"
                    if any(not name.startswith(prefix) for name in files):
                        raise ValueError(
                            "更新 ZIP 必须只包含与目标 Skill 同名的顶层目录。"
                        )
                    files = {name[len(prefix) :]: data for name, data in files.items()}
                if not any(n in files for n in ("SKILL.md", "skill.md")):
                    raise ValueError("更新后的 Skill 缺少 SKILL.md。")
                file_paths = set(files)
                if any(
                    str(parent) in file_paths
                    for name in files
                    for parent in Path(name).parents
                    if str(parent) != "."
                ):
                    raise ValueError("更新后的文件与目录路径冲突。")
            else:
                raise ValueError("不支持的 Skill 操作。")
            result.update(
                ok=True,
                operation_status="awaiting_confirmation",
                message="候选内容已准备，尚未安装或更新。",
                data={"skill_name": ", ".join(targets) if targets else skill_name},
                scan=report.to_dict(),
            )
            return result, PreparedInstall(
                manager,
                files,
                result,
                operation,
                skill_name,
                relative,
                tuple(targets),
                previous_hash,
            )
        except InspectionError as exc:
            report.incomplete(exc.code, str(exc))
            result["error"] = str(exc)
        except (ValueError, FileExistsError) as exc:
            if report.status != "complete":
                report.incomplete("invalid_input", "输入文件无法完成检查。")
            result["error"] = str(exc)
        except Exception:
            logger.exception("Skill preparation failed")
            report.incomplete("preparation_error", "候选内容准备失败。")
            result["error"] = "准备失败，未安装或更新 Skill。"
        return result | {"scan": report.to_dict()}, None

    def _commit(self, prepared: PreparedInstall) -> dict:
        result = {**prepared.result}
        result.pop("message", None)
        if prepared.consumed:
            return result | {
                "ok": False,
                "operation_status": "not_performed",
                "error": "该安装请求已结束，请重新提交。",
            }
        prepared.consumed = True
        writing = False
        try:
            manager, files = prepared.manager, prepared.files
            root = Path(manager.skills_root).resolve()
            if prepared.operation == "install":
                for name in prepared.targets:
                    if _target(root, name).exists():
                        raise FileExistsError("同名 Skill 已存在，请重新提交更新请求。")
                with tempfile.TemporaryDirectory(prefix="skill_scanned_") as tmp:
                    snapshot = Path(tmp) / "scanned.zip"
                    with zipfile.ZipFile(snapshot, "w", zipfile.ZIP_STORED) as zf:
                        for name, content in files.items():
                            zf.writestr(name, content)
                    kwargs = (
                        {"overwrite": False}
                        if "overwrite"
                        in inspect.signature(manager.install_skill_from_zip).parameters
                        else {}
                    )
                    writing = True
                    installed_name = manager.install_skill_from_zip(
                        str(snapshot), **kwargs
                    )
                for name in prepared.targets:
                    installed_dir = _target(root, name)
                    if installed_dir.is_dir():
                        _normalize_permissions(installed_dir, root)
                        if os.name == "nt" and inspect_skill_permission_issue(
                            installed_dir, root
                        ):
                            prefix = f"{name}/"
                            skill_files = {
                                k[len(prefix) :]: v
                                for k, v in files.items()
                                if k.startswith(prefix)
                            }
                            if skill_files:
                                _replace_tree(skill_files, installed_dir)
                result["data"] = {"skill_name": installed_name}
            else:
                target = _target(root, prepared.skill_name)
                if _fingerprint(load_directory(target)) != prepared.previous_hash:
                    raise ValueError("等待期间原 Skill 已改变，请重新提交并审查。")
                writing = True
                if prepared.operation == "file":
                    _replace_file(files[prepared.relative], target / prepared.relative)
                    _normalize_permissions(target, root)
                else:
                    _replace_tree(files, target)
                result["data"] = {
                    "skill_name": prepared.skill_name,
                    "files": len(files),
                }
            result.update(
                ok=True,
                operation_status="completed",
                message="Skill 已安装或更新；下一次请求生效。",
            )
        except (ValueError, FileExistsError) as exc:
            result.update(
                ok=False,
                error=str(exc),
                operation_status="failed" if writing else "not_performed",
            )
        except Exception:
            logger.exception("Skill commit failed")
            result.update(
                ok=False,
                error="安装或更新失败，请查看日志；扫描结果已保留。",
                operation_status="failed" if writing else "not_performed",
            )
        finally:
            prepared.files.clear()
        return result
