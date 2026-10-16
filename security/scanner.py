"""Bounded, offline inspection. Skill content is data and is never executed."""

from __future__ import annotations

import ast
import hashlib
import io
import os
import re
import stat
import struct
import time
import unicodedata
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

RULESET_VERSION = "1"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_FINDINGS = 100
MAX_SCAN_SECONDS = 15
_NAME = re.compile(r"^[\w.-]+$")
_SECRET = re.compile(
    r"secret|token|password|credential|api[_-]?key|\.env|\.ssh|\.aws", re.IGNORECASE
)
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".whl"}
_BINARY_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo", ".wasm", ".bin"}


def display_path(value: str) -> str:
    # Paths also come from untrusted bundles. Do not pass invisible controls to a model/UI.
    return "".join(
        c if not unicodedata.category(c).startswith("C") else f"\\u{ord(c):04x}"
        for c in value
    )[:240]


@dataclass
class ScanReport:
    status: str = "not_started"
    decision: str = "block"
    mode: str = "enforce"
    ruleset_version: str = RULESET_VERSION
    sha256: str = ""
    files_scanned: int = 0
    files_total: int = 0
    findings: list[dict] = field(default_factory=list)
    limitations: list[dict] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)

    def finding(
        self, rule: str, severity: str, path: str, line: int, message: str
    ) -> None:
        item = {
            "rule": rule,
            "severity": severity,
            "file": display_path(path),
            "line": line,
            "message": message,
        }
        if item in self.findings:
            return
        if len(self.findings) >= MAX_FINDINGS:
            raise InspectionError("finding_limit", "风险项数量超过扫描上限。")
        self.findings.append(item)

    def incomplete(self, code: str, message: str, path: str = "") -> None:
        self.status = "incomplete"
        self.decision = "block"
        if len(self.limitations) < MAX_FINDINGS:
            self.limitations.append(
                {"code": code, "message": message, "file": display_path(path)}
            )

    def finish(self) -> None:
        if self.limitations:
            self.status, self.decision = "incomplete", "block"
        else:
            self.status = "complete"
            blocked = any(f["severity"] == "high" for f in self.findings)
            self.decision = (
                "block"
                if blocked and self.mode == "enforce"
                else ("warn" if self.findings else "allow")
            )

    def to_dict(self) -> dict:
        result = asdict(self)
        result["risk"] = (
            "high"
            if any(f["severity"] == "high" for f in self.findings)
            else "warning"
            if self.findings
            else "none_detected"
        )
        return result


class InspectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def valid_skill_name(name: str) -> bool:
    return bool(name and name not in {".", ".."} and _NAME.fullmatch(name))


def member_path(name: str) -> str:
    name = name.replace("\\", "/")
    parts = name.rstrip("/").split("/")
    if (
        not name
        or name.startswith("/")
        or ":" in name
        or any(p in {"", ".", ".."} or p.endswith((" ", ".")) for p in parts)
        or any(unicodedata.category(c).startswith("C") for c in name)
        or len(parts) > 32
        or len(name) > 500
    ):
        raise InspectionError("unsafe_path", "文件路径不安全或超过路径上限。")
    return "/".join(parts)


def read_bounded(path: Path, limit: int) -> bytes:
    # Refuse symlinks and special files before opening, including FIFOs.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise InspectionError(
            "unsupported_file", "只支持普通文件，不接受符号链接或特殊文件。"
        )
    if info.st_size > limit:
        raise InspectionError("size_limit", "文件大小超过扫描上限。")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InspectionError("unsupported_file", "只支持普通文件。")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InspectionError("size_limit", "文件大小超过扫描上限。")
    return data


def _zip_directory_bounds(data: bytes) -> None:
    # Bound central-directory allocation before ZipFile constructs ZipInfo objects.
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        raise InspectionError("invalid_zip", "ZIP 目录损坏。")
    _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4s4H2IH", data, end
    )
    if disk or start_disk or disk_count != count or count == 65535:
        raise InspectionError("unsupported_zip", "不支持分卷或 ZIP64 压缩包。")
    if count > MAX_ENTRIES or size > 2 * 1024 * 1024:
        raise InspectionError("entry_limit", "ZIP 目录超过扫描上限。")
    if end + 22 + comment != len(data) or offset + size != end:
        raise InspectionError("invalid_zip", "ZIP 目录边界不一致。")
    pos, observed = offset, 0
    while pos < end:
        if data[pos : pos + 4] != b"PK\x01\x02" or pos + 46 > end:
            raise InspectionError("invalid_zip", "ZIP 目录损坏。")
        name_len, extra_len, comment_len = struct.unpack_from("<3H", data, pos + 28)
        pos += 46 + name_len + extra_len + comment_len
        observed += 1
        if observed > MAX_ENTRIES:
            raise InspectionError("entry_limit", "ZIP 文件数量超过扫描上限。")
    if pos != end or observed != count:
        raise InspectionError("invalid_zip", "ZIP 文件数量与目录声明不一致。")


def load_zip(path: Path) -> dict[str, bytes]:
    data = read_bounded(path, MAX_ARCHIVE_BYTES)
    _zip_directory_bounds(data)
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = member_path(info.filename)
            key = name.casefold()
            if key in seen:
                raise InspectionError(
                    "duplicate_path", "ZIP 包含重复或大小写冲突的路径。"
                )
            seen.add(key)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
                raise InspectionError(
                    "unsupported_zip_member", "不支持链接、特殊文件或加密 ZIP 成员。"
                )
            if info.is_dir():
                continue
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise InspectionError("size_limit", "ZIP 解压后大小超过扫描上限。")
            if info.file_size > max(1, info.compress_size) * 200:
                raise InspectionError("compression_limit", "ZIP 压缩比超过扫描上限。")
            with zf.open(info) as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) != info.file_size:
                raise InspectionError("invalid_zip", "ZIP 成员大小与声明不一致。")
            files[name] = content
    file_keys = {p.casefold() for p in files}
    for name in files:
        if any(
            str(parent).casefold() in file_keys
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise InspectionError("conflicting_path", "ZIP 包含文件与目录冲突。")
    if not files:
        raise InspectionError("empty_bundle", "压缩包没有可安装文件。")
    return files


def load_directory(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise InspectionError("unsafe_path", "Skill 必须是本地普通目录。")
    files: dict[str, bytes] = {}
    entries, total = 0, 0
    started = time.monotonic()
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as children:
            for entry in children:
                entries += 1
                if (
                    entries > MAX_ENTRIES
                    or time.monotonic() - started > MAX_SCAN_SECONDS
                ):
                    raise InspectionError("resource_limit", "目录遍历超过扫描上限。")
                path = Path(entry.path)
                rel = member_path(path.relative_to(root).as_posix())
                if entry.is_symlink():
                    raise InspectionError("unsupported_file", "Skill 中包含符号链接。")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    files[rel] = read_bounded(path, MAX_FILE_BYTES)
                    total += len(files[rel])
                    if total > MAX_TOTAL_BYTES:
                        raise InspectionError(
                            "size_limit", "Skill 总大小超过扫描上限。"
                        )
    return files


# Original, deliberately small rules. Match an action and its target, not isolated keywords.
_TEXT_RULES = [
    (
        "instruction_override",
        "high",
        re.compile(
            r"(?:ignore|bypass|disregard)\s+(?:all\s+)?(?:safety|security)\s+(?:rules?|checks?|constraints?)|(?:忽略|绕过|无视)(?:所有)?(?:安全规则|安全检查|安全限制)",
            re.IGNORECASE,
        ),
        "指令要求绕过安全约束。",
    ),
    (
        "covert_exfiltration",
        "high",
        re.compile(
            r"(?:silently|secretly|covertly)\s+(?:send|upload|transmit)\b.{0,100}(?:token|secret|credential|conversation|chat)|(?:偷偷|秘密|悄悄).{0,30}(?:上传|发送|外传).{0,50}(?:密钥|令牌|凭据|聊天|会话)",
            re.IGNORECASE,
        ),
        "指令要求秘密外传敏感信息。",
    ),
    (
        "download_execute",
        "high",
        re.compile(
            r"\b(?:curl|wget)\b[^\n|]{0,500}\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b",
            re.IGNORECASE,
        ),
        "下载内容直接通过管道交给 shell 执行。",
    ),
    (
        "destructive_command",
        "high",
        re.compile(r"\brm\s+(?:-[a-zA-Z]+\s+){1,3}(?:/\*?|~/?|\$HOME/?)(?:\s|[;`]|$)"),
        "命令试图删除根目录或用户主目录内容。",
    ),
    (
        "credential_upload",
        "high",
        re.compile(
            r"\bcurl\b.{0,200}(?:--data(?:-binary|-raw)?|-d|-F|--upload-file|-T)\s*[^\n]{0,100}@?(?:~/|\$HOME/)?(?:\.env\b|\.ssh/|\.aws/)",
            re.IGNORECASE,
        ),
        "网络命令将敏感文件用作上传内容。",
    ),
    (
        "instruction_override",
        "warning",
        re.compile(
            r"ignore\s+(?:all\s+)?previous\s+instructions?|忽略(?:所有)?(?:之前|以前)的?指令",
            re.IGNORECASE,
        ),
        "出现覆盖先前指令的表述，需要核对上下文。",
    ),
]
_NEGATED = re.compile(
    r"(?:\b(?:do not|don't|never|avoid|must not)\s+(?:(?:run|execute)\s+)?|(?:不要|禁止|切勿|不得)(?:运行|执行)?)$",
    re.IGNORECASE,
)


def _text_scan(path: str, text: str, report: ScanReport) -> None:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    if normalized != unicodedata.normalize("NFKC", text):
        report.finding(
            "hidden_characters", "warning", path, 1, "文本包含不可见格式字符。"
        )
    for line_no, line in enumerate(normalized.splitlines(), 1):
        # A bounded line prevents a long input from multiplying regex work.
        for offset in range(0, len(line), 4096):
            window = line[max(0, offset - 600) : offset + 4096]
            for rule, severity, pattern, message in _TEXT_RULES:
                for match in pattern.finditer(window):
                    # Only a directly negated action is contextual advice. An unrelated
                    # "do not" elsewhere must not suppress a real execution instruction.
                    prefix = window[: match.start()].rstrip("` ")
                    triage = (
                        "warning"
                        if _NEGATED.search(prefix + " ") or _NEGATED.search(prefix)
                        else severity
                    )
                    report.finding(rule, triage, path, line_no, message)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


class _PythonScan(ast.NodeVisitor):
    def __init__(self, path: str, report: ScanReport):
        self.path, self.report = path, report
        self.aliases: dict[str, str] = {}
        self.values: dict[str, set[str]] = {}
        self.deadline = time.monotonic() + MAX_SCAN_SECONDS
        self.steps = 0

    def _budget(self) -> None:
        self.steps += 1
        if self.steps > 200_000 or time.monotonic() > self.deadline:
            raise InspectionError("ast_work_limit", "Python 数据流分析超过工作量上限。")

    def visit(self, node: ast.AST):
        self._budget()
        return super().visit(node)

    def name(self, node: ast.AST) -> str:
        raw = _dotted(node)
        root, dot, tail = raw.partition(".")
        return self.aliases.get(root, root) + (dot + tail if dot else "")

    def emit(self, node: ast.AST, rule: str, severity: str, message: str) -> None:
        self.report.finding(
            rule, severity, self.path, getattr(node, "lineno", 1), message
        )

    def source(self, node: ast.AST) -> set[str]:
        self._budget()
        if isinstance(node, ast.Name):
            return self.values.get(node.id, set())
        tags: set[str] = set()
        if isinstance(node, ast.Call):
            name = self.name(node.func)
            if name in {"os.getenv", "os.environ.get", "open", "pathlib.Path"} and any(
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and _SECRET.search(a.value)
                for a in node.args[:1]
            ):
                tags.add("secret")
            if name.startswith(
                ("requests.", "httpx.", "urllib.request.")
            ) and name.rsplit(".", 1)[-1] in {"get", "post", "urlopen", "request"}:
                tags.add("remote")
            if name in {"base64.b64decode", "base64.decodebytes", "bytes.fromhex"}:
                tags.add("encoded")
        if (
            isinstance(node, ast.Subscript)
            and self.name(node.value) == "os.environ"
            and isinstance(node.slice, ast.Constant)
            and _SECRET.search(str(node.slice.value))
        ):
            tags.add("secret")
        for child in ast.iter_child_nodes(node):
            tags |= self.source(child)
        return tags

    def visit_Import(self, node: ast.Import) -> None:
        for entry in node.names:
            self.aliases[entry.asname or entry.name.split(".")[0]] = (
                entry.name if entry.asname else entry.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for entry in node.names:
            self.aliases[entry.asname or entry.name] = f"{node.module}.{entry.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        tags = self.source(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.values[target.id] = tags

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            if isinstance(node.target, ast.Name):
                self.values[node.target.id] = self.source(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        aliases, values = self.aliases.copy(), self.values.copy()
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.values.pop(arg.arg, None)
            self.aliases.pop(arg.arg, None)
        self.generic_visit(node)
        self.aliases, self.values = aliases, values

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        name = self.name(node.func)
        execution = name in {
            "exec",
            "eval",
            "builtins.exec",
            "builtins.eval",
            "os.system",
            "os.popen",
        } or name.startswith("subprocess.")
        if execution:
            payload = list(node.args)
            payload.extend(
                k.value for k in node.keywords if k.arg in {"args", "command", "source"}
            )
            # A shell/interpreter can execute stdin; ordinary subprocess input is data.
            if any(
                isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and re.search(r"(?:^|/)(?:sh|bash|zsh|python[\d.]*)$", n.value)
                for a in payload
                for n in ast.walk(a)
            ):
                payload.extend(k.value for k in node.keywords if k.arg == "input")
            tags = set().union(*(self.source(a) for a in payload))
            if tags & {"remote", "encoded"}:
                self.emit(
                    node,
                    "dynamic_execution_chain",
                    "high",
                    "下载或解码后的内容流向代码/命令执行调用。",
                )
            else:
                self.emit(
                    node,
                    "execution_capability",
                    "warning",
                    "代码包含动态执行或子进程能力，需要核对用途。",
                )
        if name.startswith(("requests.", "httpx.", "urllib.request.")):
            # Authentication headers are an ordinary API use. Inspect URL and payload only.
            payload = list(node.args)
            payload.extend(
                k.value
                for k in node.keywords
                if k.arg in {"data", "json", "files", "params", "url"}
            )
            if any("secret" in self.source(a) for a in payload):
                self.emit(
                    node,
                    "sensitive_data_upload",
                    "high",
                    "敏感环境变量或敏感文件内容流向网络地址/请求载荷。",
                )
        if name in {"pickle.load", "pickle.loads", "marshal.loads", "yaml.unsafe_load"}:
            self.emit(
                node,
                "unsafe_deserialization",
                "warning",
                "代码使用可构造对象或执行代码的反序列化接口。",
            )
        self.generic_visit(node)


def _valid_png(content: bytes) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset, chunks, has_data = 8, 0, False
    while offset + 12 <= len(content):
        size = int.from_bytes(content[offset : offset + 4], "big")
        end = offset + 12 + size
        if end > len(content):
            return False
        kind = content[offset + 4 : offset + 8]
        payload = content[offset + 8 : end - 4]
        crc = int.from_bytes(content[end - 4 : end], "big")
        if zlib.crc32(content[offset + 4 : end - 4]) != crc:
            return False
        if chunks == 0 and (kind != b"IHDR" or size != 13):
            return False
        if kind == b"IHDR" and (
            chunks
            or not int.from_bytes(payload[:4], "big")
            or not int.from_bytes(payload[4:8], "big")
        ):
            return False
        if kind == b"IDAT":
            has_data = True
        if kind == b"IEND":
            return has_data and size == 0 and end == len(content)
        offset, chunks = end, chunks + 1
    return False


def scan_files(files: dict[str, bytes], report: ScanReport) -> ScanReport:
    report.status = "running"
    report.files_total = len(files)
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        encoded_path = path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(4, "big") + encoded_path)
        digest.update(len(content).to_bytes(8, "big") + content)
    report.sha256 = digest.hexdigest()
    started = time.monotonic()
    try:
        for path, content in sorted(files.items()):
            if time.monotonic() - started > MAX_SCAN_SECONDS:
                raise InspectionError("scan_timeout", "扫描超过时间上限。")
            suffix = PurePosixPath(path).suffix.lower()
            if (
                suffix in _ARCHIVE_SUFFIXES
                or suffix in _BINARY_SUFFIXES
                or content.startswith((b"PK\x03\x04", b"\x7fELF", b"MZ", b"\x1f\x8b"))
            ):
                report.incomplete(
                    "unsupported_content", "初版不分析嵌套压缩包或可执行二进制。", path
                )
                continue
            # Raster resources are inventoried, not represented as security-verified code.
            if suffix == ".png" and _valid_png(content):
                _text_scan(path, content.decode("utf-8", errors="replace"), report)
                report.attachments.append(display_path(path))
                continue
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                report.incomplete(
                    "unsupported_encoding", "文件不是可检查的 UTF-8 文本。", path
                )
                continue
            if "\x00" in text:
                report.incomplete(
                    "binary_content", "文件包含无法作为文本检查的二进制内容。", path
                )
                continue
            _text_scan(path, text, report)
            if suffix == ".py":
                if len(text) > 250_000:
                    report.incomplete(
                        "ast_size_limit", "Python 源码超过 AST 分析上限。", path
                    )
                    continue
                try:
                    tree = ast.parse(text)
                    if sum(1 for _ in ast.walk(tree)) > 50_000:
                        raise InspectionError(
                            "ast_node_limit", "Python AST 节点数超过分析上限。"
                        )
                    _PythonScan(path, report).visit(tree)
                except (SyntaxError, RecursionError):
                    report.incomplete(
                        "python_parse_error", "Python 代码无法完成 AST 分析。", path
                    )
                    continue
            report.files_scanned += 1
    except InspectionError as exc:
        report.incomplete(exc.code, str(exc))
    report.finish()
    return report


_ADMIN_ONLY_SIDS = {"SY", "BA", "LA", "S-1-5-18", "S-1-5-32-544"}
_SDDL_RIGHT_MASKS = {
    "GA": 0x001F01FF,
    "FA": 0x001F01FF,
    "GR": 0x00120089,
    "FR": 0x00120089,
    "GX": 0x001200A0,
    "FX": 0x001200A0,
    "GW": 0x00120116,
    "FW": 0x00120116,
}


def _parse_ace_flags(flags: str) -> set[str]:
    cleaned = flags.strip().upper()
    return {
        cleaned[i : i + 2]
        for i in range(0, len(cleaned), 2)
        if len(cleaned[i : i + 2]) == 2
    }


def _rights_to_access_mask(rights: str) -> int:
    cleaned = rights.strip()
    hex_match = re.fullmatch(r"0x[0-9a-fA-F]+", cleaned)
    if hex_match:
        mask = int(cleaned, 16)
        if mask & 0x10000000:
            mask |= 0x001F01FF
        if mask & 0x80000000:
            mask |= 0x00120089
        if mask & 0x20000000:
            mask |= 0x001200A0
        if mask & 0x40000000:
            mask |= 0x00120116
        return mask
    upper = cleaned.upper()
    mask = 0
    for idx in range(0, len(upper), 2):
        token = upper[idx : idx + 2]
        if len(token) == 2:
            mask |= _SDDL_RIGHT_MASKS.get(token, 0)
    return mask


def _get_windows_sddl(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        info = 0x00000001 | 0x00000004  # OWNER | DACL
        sd = wintypes.LPVOID()
        rc = advapi32.GetNamedSecurityInfoW(
            wintypes.LPCWSTR(str(path)),
            1,  # SE_FILE_OBJECT
            info,
            None,
            None,
            None,
            None,
            ctypes.byref(sd),
        )
        if rc != 0 or not sd.value:
            return None
        try:
            buf = wintypes.LPWSTR()
            if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                sd, 1, info, ctypes.byref(buf), None
            ):
                return None
            try:
                return buf.value
            finally:
                kernel32.LocalFree(buf)
        finally:
            kernel32.LocalFree(sd)
    except (OSError, AttributeError, ValueError):
        return None


def _sddl_components(
    sddl: str | None,
    *,
    is_dir: bool = True,
) -> tuple[str, str, set[str], set[str]]:
    if not sddl:
        return "", "", set(), set()
    owner_match = re.search(r"O:(.*?)(?=(?:[GDS]:|\(|$))", sddl)
    owner = owner_match.group(1).strip() if owner_match else ""
    dacl_match = re.search(r"D:([A-Z]*)", sddl)
    dacl_flags = dacl_match.group(1) if dacl_match else ""
    allow_masks: dict[str, int] = {}
    deny_masks: dict[str, int] = {}
    for ace_type, raw_flags, rights, raw_trustee in re.findall(
        r"\(([AD]);([^;]*);([^;]*);[^;]*;[^;]*;([^)]+)\)", sddl
    ):
        if "IO" in _parse_ace_flags(raw_flags):
            continue
        mask = _rights_to_access_mask(rights)
        if mask == 0:
            continue
        trustee = raw_trustee.strip()
        resolved = owner if trustee in {"OW", "S-1-3-4"} and owner else trustee
        if ace_type == "D":
            deny_masks[resolved] = deny_masks.get(resolved, 0) | mask
        else:
            allow_masks[resolved] = allow_masks.get(resolved, 0) | mask
    required_mask = (0x1 | 0x20) if is_dir else 0x1
    deny_trustees = {
        trustee for trustee, mask in deny_masks.items() if (mask & required_mask) != 0
    }
    allow_trustees = {
        trustee
        for trustee, mask in allow_masks.items()
        if ((mask & ~deny_masks.get(trustee, 0)) & required_mask) == required_mask
    }
    return owner, dacl_flags, allow_trustees, deny_trustees


def _check_windows_sddl(
    path_sddl: str | None,
    root_sddl: str | None = None,
    *,
    is_dir: bool = True,
) -> str | None:
    if not path_sddl:
        return None
    _, path_flags, path_trustees, path_denied = _sddl_components(
        path_sddl, is_dir=is_dir
    )
    _, root_flags, root_trustees, _ = _sddl_components(root_sddl, is_dir=True)
    if "P" in path_flags and "P" not in root_flags:
        return "NTFS ACL 已禁止继承父目录权限"
    root_non_admin = root_trustees - _ADMIN_ONLY_SIDS
    if path_denied & (root_non_admin or {"BU", "AU", "WD", "IU"}):
        return "NTFS ACL 显式拒绝普通用户读取或遍历权限"
    if not path_trustees:
        return "NTFS ACL 未授予任何读取或遍历权限"
    if path_trustees <= _ADMIN_ONLY_SIDS and (not root_trustees or root_non_admin):
        return "NTFS ACL 仅允许管理员或系统访问，未包含普通用户权限"
    if root_non_admin and not ((path_trustees - _ADMIN_ONLY_SIDS) & root_non_admin):
        return "NTFS ACL 缺少父目录授予的普通用户访问权限"
    return None


def inspect_skill_permission_issue(
    skill_dir: Path,
    root: Path,
    *,
    budget: list[float] | None = None,
) -> str | None:
    try:
        dir_stat = skill_dir.lstat()
        if stat.S_ISLNK(dir_stat.st_mode):
            return "目录为符号链接"
        if not stat.S_ISDIR(dir_stat.st_mode):
            return None
    except PermissionError:
        return "目录无读取或访问权限（PermissionError）"
    except OSError:
        return None

    root_sddl = _get_windows_sddl(root) if os.name == "nt" else None
    if os.name == "nt":
        win_issue = _check_windows_sddl(
            _get_windows_sddl(skill_dir), root_sddl, is_dir=True
        )
        if win_issue:
            return win_issue
        root_uid = 0
        required_dir_bits = 0
        required_file_bits = 0
    else:
        try:
            root_stat = root.stat()
            root_mode = stat.S_IMODE(root_stat.st_mode)
            root_uid = root_stat.st_uid
        except OSError:
            root_mode = 0o755
            root_uid = 0
        required_dir_bits = (root_mode & 0o055) | 0o500
        required_file_bits = (root_mode & 0o044) | 0o400

    state = budget if budget is not None else [0.0, time.monotonic()]
    pending = [skill_dir]
    while pending:
        current = pending.pop()
        try:
            current_stat = current.lstat()
        except PermissionError:
            return "目录无读取或访问权限（PermissionError）"
        except OSError:
            continue
        if stat.S_ISLNK(current_stat.st_mode):
            return "目录内包含符号链接"
        if os.name != "nt":
            current_mode = stat.S_IMODE(current_stat.st_mode)
            if (current_mode & required_dir_bits) != required_dir_bits:
                return f"目录权限为 {oct(current_mode)}，低于父目录的读取或遍历权限范围"
            if current_stat.st_uid != root_uid and (
                current_stat.st_uid == 0 or (current_mode & 0o005) != 0o005
            ):
                return "目录所有者与父目录不一致且缺少普通用户访问权限"
            if not os.access(current, os.R_OK | os.X_OK):
                return "目录缺少当前用户读取或遍历权限"
        elif current != skill_dir:
            win_issue = _check_windows_sddl(
                _get_windows_sddl(current), root_sddl, is_dir=True
            )
            if win_issue:
                return win_issue
        try:
            with os.scandir(current) as children:
                for entry in children:
                    state[0] += 1
                    if (
                        state[0] > MAX_ENTRIES
                        or time.monotonic() - state[1] > MAX_SCAN_SECONDS
                    ):
                        return "目录条目过多或检查超时，无法完整确认权限范围"
                    entry_path = Path(entry.path)
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except PermissionError:
                        return "目录内条目无读取权限（PermissionError）"
                    except OSError:
                        continue
                    if stat.S_ISLNK(entry_stat.st_mode):
                        return "目录内包含符号链接"
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append(entry_path)
                    elif stat.S_ISREG(entry_stat.st_mode):
                        if os.name != "nt":
                            file_mode = stat.S_IMODE(entry_stat.st_mode)
                            if (file_mode & required_file_bits) != required_file_bits:
                                return f"文件 {entry.name} 权限为 {oct(file_mode)}，缺少与父目录匹配的读取权限"
                            if entry_stat.st_uid != root_uid and (
                                entry_stat.st_uid == 0 or (file_mode & 0o004) != 0o004
                            ):
                                return f"文件 {entry.name} 所有者与父目录不一致且缺少读取权限"
                            if not os.access(entry_path, os.R_OK):
                                return f"文件 {entry.name} 缺少读取权限"
                        else:
                            win_issue = _check_windows_sddl(
                                _get_windows_sddl(entry_path),
                                root_sddl,
                                is_dir=False,
                            )
                            if win_issue:
                                return win_issue
                            if not os.access(entry_path, os.R_OK):
                                return f"文件 {entry.name} 缺少读取权限"
        except PermissionError:
            return "目录无遍历权限（PermissionError）"
        except OSError:
            continue
    return None


def _emit_legacy_warning(
    report: ScanReport,
    warnings: list[dict],
    *,
    name: str,
    label: str,
    issue: str,
    is_plugin: bool,
) -> None:
    warnings.append({"name": name, "path": label, "reason": issue})
    if len(report.findings) < MAX_FINDINGS:
        kind = "插件" if is_plugin else "Skill"
        report.finding(
            "legacy_skill_permission_risk",
            "warning",
            label,
            1,
            f"已安装的旧 {kind} '{name}' 存在权限范围异常（{issue}），切换非管理员或普通用户启动时可能导致无法读取并卡死回复链。",
        )


def audit_installed_skills(
    root: Path,
    report: ScanReport,
    *,
    plugins_root: Path | None = None,
) -> list[dict]:
    warnings: list[dict] = []
    budget = [0.0, time.monotonic()]
    roots: list[tuple[Path, str, bool]] = [(root, "", False)]
    if plugins_root is not None:
        roots.append((plugins_root, "plugins/", True))

    for base_root, prefix, is_plugin_root in roots:
        try:
            if not base_root.exists():
                continue
            candidates = []
            with os.scandir(base_root) as it:
                for item in it:
                    budget[0] += 1
                    if (
                        budget[0] > MAX_ENTRIES
                        or time.monotonic() - budget[1] > MAX_SCAN_SECONDS
                    ):
                        _emit_legacy_warning(
                            report,
                            warnings,
                            name=base_root.name,
                            label=prefix.rstrip("/") or base_root.name,
                            issue="已安装目录数量超过上限或检查超时",
                            is_plugin=is_plugin_root,
                        )
                        break
                    candidates.append(item)
            candidates.sort(key=lambda item: item.name)
        except FileNotFoundError:
            continue
        except OSError as exc:
            _emit_legacy_warning(
                report,
                warnings,
                name=base_root.name,
                label=prefix.rstrip("/") or base_root.name,
                issue=f"根目录无读取或遍历权限（{exc.__class__.__name__}）",
                is_plugin=is_plugin_root,
            )
            continue

        for entry in candidates:
            if not valid_skill_name(entry.name):
                continue
            entry_path = Path(entry.path)
            label = f"{prefix}{entry.name}"
            try:
                is_symlink = entry.is_symlink()
                is_dir = True if is_symlink else entry.is_dir(follow_symlinks=False)
            except PermissionError:
                is_symlink = False
                is_dir = True
            except OSError:
                continue
            if not is_dir:
                continue
            if is_symlink:
                _emit_legacy_warning(
                    report,
                    warnings,
                    name=entry.name,
                    label=label,
                    issue="目录为符号链接",
                    is_plugin=is_plugin_root,
                )
                continue

            if is_plugin_root:
                target_dir = entry_path / "skills"
                try:
                    entry_stat = entry_path.lstat()
                    if os.name != "nt":
                        root_stat = base_root.stat()
                        req = (stat.S_IMODE(root_stat.st_mode) & 0o055) | 0o500
                        cur_mode = stat.S_IMODE(entry_stat.st_mode)
                        owner_bad = entry_stat.st_uid != root_stat.st_uid and (
                            entry_stat.st_uid == 0 or (cur_mode & 0o005) != 0o005
                        )
                        if (
                            (cur_mode & req) != req
                            or owner_bad
                            or not os.access(entry_path, os.R_OK | os.X_OK)
                        ):
                            _emit_legacy_warning(
                                report,
                                warnings,
                                name=entry.name,
                                label=label,
                                issue="插件目录缺少读取或遍历权限",
                                is_plugin=True,
                            )
                            continue
                    else:
                        win_issue = _check_windows_sddl(
                            _get_windows_sddl(entry_path),
                            _get_windows_sddl(base_root),
                        )
                        if win_issue:
                            _emit_legacy_warning(
                                report,
                                warnings,
                                name=entry.name,
                                label=label,
                                issue=win_issue,
                                is_plugin=True,
                            )
                            continue
                    target_dir.lstat()
                except FileNotFoundError:
                    continue
                except PermissionError:
                    _emit_legacy_warning(
                        report,
                        warnings,
                        name=entry.name,
                        label=label,
                        issue="插件目录或 skills 子目录无访问权限（PermissionError）",
                        is_plugin=True,
                    )
                    continue
                except OSError as exc:
                    _emit_legacy_warning(
                        report,
                        warnings,
                        name=entry.name,
                        label=label,
                        issue=f"插件目录或 skills 子目录访问异常（{exc.__class__.__name__}）",
                        is_plugin=True,
                    )
                    continue
            else:
                target_dir = entry_path

            issue = inspect_skill_permission_issue(target_dir, base_root, budget=budget)
            if issue:
                _emit_legacy_warning(
                    report,
                    warnings,
                    name=entry.name,
                    label=label,
                    issue=issue,
                    is_plugin=is_plugin_root,
                )

    if warnings and report.status == "complete":
        report.finish()
    return warnings
