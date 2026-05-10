from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from app.errors import FsError
from app.models import FindRequest, GrepFlags, RequestContext, ShellRunRequest, ShellRunResponse
from app.path_utils import normalize_virtual_path

if TYPE_CHECKING:
    from app.fs import OpenSearchFS


WRITE_COMMANDS = {
    "mkdir",
    "rm",
    "rmdir",
    "mv",
    "cp",
    "touch",
    "tee",
    "sed",
    "awk",
    "perl",
    "python",
    "python3",
    "node",
    "npm",
    "pip",
    "curl",
    "wget",
    "git",
}


@dataclass
class SessionState:
    session_id: str
    tenant_id: str
    collection_id: str
    cwd: str
    principals: list[str]


class ShellService:
    """Restricted bash-like shell that routes commands to OpenSearchFS."""

    def __init__(self, fs: "OpenSearchFS", max_output_bytes: int):
        self.fs = fs
        self.max_output_bytes = max_output_bytes
        self.sessions: dict[str, SessionState] = {}

    def run(self, request: ShellRunRequest) -> ShellRunResponse:
        state = self._get_or_create_session(request)
        state.principals = request.principals
        ctx = RequestContext(
            tenant_id=state.tenant_id,
            collection_id=state.collection_id,
            principals=state.principals,
        )

        try:
            stdout = self._execute_pipeline(ctx, state, request.command)
            stdout, truncated = self._limit_output(stdout)
            return ShellRunResponse(
                session_id=state.session_id,
                cwd=state.cwd,
                stdout=stdout,
                stderr="",
                exit_code=0,
                truncated=truncated,
            )
        except FsError as exc:
            return ShellRunResponse(
                session_id=state.session_id,
                cwd=state.cwd,
                stdout="",
                stderr=f"{exc.code}: {exc.message}\n",
                exit_code=1,
                truncated=False,
            )
        except ShellParseError as exc:
            return ShellRunResponse(
                session_id=state.session_id,
                cwd=state.cwd,
                stdout="",
                stderr=f"shell: {exc}\n",
                exit_code=2,
                truncated=False,
            )
        except UnsupportedCommandError as exc:
            return ShellRunResponse(
                session_id=state.session_id,
                cwd=state.cwd,
                stdout="",
                stderr=f"{exc}\n",
                exit_code=127,
                truncated=False,
            )

    def _get_or_create_session(self, request: ShellRunRequest) -> SessionState:
        key = f"{request.tenant_id}:{request.collection_id}:{request.session_id}"
        state = self.sessions.get(key)
        if state is None:
            state = SessionState(
                session_id=request.session_id,
                tenant_id=request.tenant_id,
                collection_id=request.collection_id,
                cwd=normalize_virtual_path(request.cwd),
                principals=request.principals,
            )
            self.sessions[key] = state
        return state

    def _execute_pipeline(self, ctx: RequestContext, state: SessionState, command: str) -> str:
        if _looks_like_redirection(command):
            raise FsError("EROFS", "read-only file system: redirection is disabled")

        stages = split_pipeline(command)
        if not stages:
            return ""

        current_stdout = ""
        for index, stage in enumerate(stages):
            argv = shlex.split(stage)
            if not argv:
                continue
            current_stdout = self._execute_command(ctx, state, argv, current_stdout if index > 0 else None)
        return current_stdout

    def _execute_command(
        self,
        ctx: RequestContext,
        state: SessionState,
        argv: list[str],
        stdin: str | None,
    ) -> str:
        cmd = argv[0]
        if cmd in WRITE_COMMANDS:
            raise FsError("EROFS", f"read-only file system: {cmd}")

        dispatch: dict[str, Callable[[RequestContext, SessionState, list[str], str | None], str]] = {
            "pwd": self._cmd_pwd,
            "cd": self._cmd_cd,
            "ls": self._cmd_ls,
            "cat": self._cmd_cat,
            "head": self._cmd_head,
            "tail": self._cmd_tail,
            "find": self._cmd_find,
            "grep": self._cmd_grep,
            "rg": self._cmd_grep,
            "stat": self._cmd_stat,
            "wc": self._cmd_wc,
            "sort": self._cmd_sort,
        }
        handler = dispatch.get(cmd)
        if handler is None:
            raise UnsupportedCommandError(f"{cmd}: command not supported by restricted OpenSearchFS shell")
        return handler(ctx, state, argv, stdin)

    def _cmd_pwd(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        return state.cwd + "\n"

    def _cmd_cd(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        if len(argv) > 2:
            raise ShellParseError("cd accepts at most one path")
        target = argv[1] if len(argv) == 2 else "/"
        normalized = normalize_virtual_path(target, state.cwd)
        stat = self.fs.stat(ctx, normalized)
        if stat.type != "dir":
            raise FsError("ENOTDIR", f"not a directory: {normalized}")
        state.cwd = normalized
        return ""

    def _cmd_ls(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        long = False
        paths: list[str] = []
        for arg in argv[1:]:
            if arg.startswith("-"):
                if "l" in arg:
                    long = True
                continue
            paths.append(arg)
        if not paths:
            paths = ["."]

        chunks: list[str] = []
        for raw_path in paths:
            normalized = normalize_virtual_path(raw_path, state.cwd)
            stat = self.fs.stat(ctx, normalized)
            if stat.type == "file":
                chunks.append(self._format_ls_entry(stat, long))
                continue
            entries = self.fs.readdir(ctx, normalized)
            for entry in entries:
                chunks.append(self._format_ls_entry(entry, long))
        return "\n".join(chunks) + ("\n" if chunks else "")

    def _cmd_cat(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        if len(argv) < 2:
            if stdin is not None:
                return stdin
            raise ShellParseError("cat requires at least one file")
        parts: list[str] = []
        for raw_path in argv[1:]:
            result = self.fs.read_file(ctx, raw_path, cwd=state.cwd)
            parts.append(result.content)
        return "".join(parts)

    def _cmd_head(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        n, paths = _parse_head_tail_args(argv[1:], default=10)
        content = self._content_from_paths_or_stdin(ctx, state, paths, stdin, "head")
        lines = content.splitlines()
        return "\n".join(lines[:n]) + ("\n" if lines[:n] else "")

    def _cmd_tail(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        n, paths = _parse_head_tail_args(argv[1:], default=10)
        content = self._content_from_paths_or_stdin(ctx, state, paths, stdin, "tail")
        lines = content.splitlines()
        return "\n".join(lines[-n:]) + ("\n" if lines[-n:] else "")

    def _cmd_find(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        root, name_glob, node_type, max_depth = _parse_find_args(argv[1:])
        request = FindRequest(
            tenant_id=ctx.tenant_id,
            collection_id=ctx.collection_id,
            principals=ctx.principals,
            root=root,
            name_glob=name_glob,
            type=node_type,
            max_depth=max_depth,
            limit=self.fs.settings.osfs_max_search_hits,
        )
        result = self.fs.find(request, cwd=state.cwd)
        return "\n".join(entry.path for entry in result.entries) + ("\n" if result.entries else "")

    def _cmd_grep(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        parsed = _parse_grep_args(argv[1:])
        if stdin is not None and not parsed.paths:
            return _local_grep(stdin, parsed.pattern, parsed.flags)

        roots = parsed.paths or ["."]
        result = self.fs.grep(ctx, parsed.pattern, roots, parsed.flags, cwd=state.cwd)
        lines: list[str] = []
        for hit in result.hits:
            if parsed.flags.files_with_matches:
                lines.append(hit.path)
            elif parsed.flags.line_numbers:
                lines.append(f"{hit.path}:{hit.line_no}:{hit.line}")
            else:
                lines.append(f"{hit.path}:{hit.line}")
        return "\n".join(lines) + ("\n" if lines else "")

    def _cmd_stat(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        if len(argv) != 2:
            raise ShellParseError("stat requires exactly one path")
        stat = self.fs.stat(ctx, argv[1], cwd=state.cwd)
        return json.dumps(stat.model_dump(mode="json"), indent=2) + "\n"

    def _cmd_wc(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        if len(argv) == 2 and argv[1] == "-l":
            if stdin is None:
                raise ShellParseError("wc -l requires piped input in this shell")
            return str(len(stdin.splitlines())) + "\n"
        raise UnsupportedCommandError("wc: only wc -l is supported")

    def _cmd_sort(self, ctx: RequestContext, state: SessionState, argv: list[str], stdin: str | None) -> str:
        if stdin is None:
            raise ShellParseError("sort requires piped input in this shell")
        reverse = "-r" in argv[1:]
        unique = "-u" in argv[1:]
        lines = stdin.splitlines()
        lines = sorted(lines, reverse=reverse)
        if unique:
            deduped: list[str] = []
            previous: str | None = None
            for line in lines:
                if line != previous:
                    deduped.append(line)
                previous = line
            lines = deduped
        return "\n".join(lines) + ("\n" if lines else "")

    def _content_from_paths_or_stdin(
        self,
        ctx: RequestContext,
        state: SessionState,
        paths: list[str],
        stdin: str | None,
        command_name: str,
    ) -> str:
        if paths:
            return "".join(self.fs.read_file(ctx, p, cwd=state.cwd).content for p in paths)
        if stdin is not None:
            return stdin
        raise ShellParseError(f"{command_name} requires a file or piped input")

    def _format_ls_entry(self, entry, long: bool) -> str:
        if not long:
            return entry.basename or entry.path
        type_char = "d" if entry.type == "dir" else "-"
        size = entry.size_bytes or 0
        mtime = entry.mtime.isoformat() if entry.mtime else "-"
        name = entry.basename or entry.path
        return f"{type_char} {size:>10} {mtime} {name}"

    def _limit_output(self, stdout: str) -> tuple[str, bool]:
        data = stdout.encode("utf-8")
        if len(data) <= self.max_output_bytes:
            return stdout, False
        truncated = data[: self.max_output_bytes].decode("utf-8", errors="ignore")
        return truncated + "\n[truncated]\n", True


class ShellParseError(Exception):
    pass


class UnsupportedCommandError(Exception):
    pass


@dataclass
class ParsedGrep:
    pattern: str
    paths: list[str]
    flags: GrepFlags


def split_pipeline(command: str) -> list[str]:
    stages: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if char == "\\":
            buf.append(char)
            escaped = True
            continue
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
            continue
        if char == "|":
            stage = "".join(buf).strip()
            if not stage:
                raise ShellParseError("empty pipeline stage")
            stages.append(stage)
            buf = []
            continue
        buf.append(char)

    if quote:
        raise ShellParseError("unterminated quote")
    stage = "".join(buf).strip()
    if stage:
        stages.append(stage)
    return stages


def _looks_like_redirection(command: str) -> bool:
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in {">", "<"}:
            return True
    return False


def _parse_head_tail_args(args: list[str], default: int) -> tuple[int, list[str]]:
    n = default
    paths: list[str] = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "-n":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("-n requires a number")
            n = int(args[idx])
        elif arg.startswith("-n") and len(arg) > 2:
            n = int(arg[2:])
        elif arg.startswith("-") and arg[1:].isdigit():
            n = int(arg[1:])
        else:
            paths.append(arg)
        idx += 1
    return max(n, 0), paths


def _parse_find_args(args: list[str]) -> tuple[str, str | None, str | None, int | None]:
    root = "."
    name_glob: str | None = None
    node_type: str | None = None
    max_depth: int | None = None

    idx = 0
    if idx < len(args) and not args[idx].startswith("-"):
        root = args[idx]
        idx += 1

    while idx < len(args):
        arg = args[idx]
        if arg == "-name":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("find -name requires a pattern")
            name_glob = args[idx]
        elif arg == "-type":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("find -type requires f or d")
            if args[idx] == "f":
                node_type = "file"
            elif args[idx] == "d":
                node_type = "dir"
            else:
                raise ShellParseError("find -type supports only f or d")
        elif arg == "-maxdepth":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("find -maxdepth requires a number")
            max_depth = int(args[idx])
        else:
            raise ShellParseError(f"unsupported find option: {arg}")
        idx += 1
    return root, name_glob, node_type, max_depth


def _parse_grep_args(args: list[str]) -> ParsedGrep:
    ignore_case = False
    recursive = False
    line_numbers = False
    files_with_matches = False
    regex = False
    include_globs: list[str] = []
    exclude_globs: list[str] = []

    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--":
            idx += 1
            break
        if arg == "--include":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("grep --include requires a glob")
            include_globs.append(args[idx])
        elif arg.startswith("--include="):
            include_globs.append(arg.split("=", 1)[1])
        elif arg == "--exclude":
            idx += 1
            if idx >= len(args):
                raise ShellParseError("grep --exclude requires a glob")
            exclude_globs.append(args[idx])
        elif arg.startswith("--exclude="):
            exclude_globs.append(arg.split("=", 1)[1])
        elif arg.startswith("-") and len(arg) > 1:
            for flag in arg[1:]:
                if flag in {"r", "R"}:
                    recursive = True
                elif flag == "i":
                    ignore_case = True
                elif flag == "n":
                    line_numbers = True
                elif flag == "l":
                    files_with_matches = True
                elif flag in {"E", "P"}:
                    regex = True
                else:
                    raise ShellParseError(f"unsupported grep flag: -{flag}")
        else:
            break
        idx += 1

    if idx >= len(args):
        raise ShellParseError("grep requires a pattern")
    pattern = args[idx]
    paths = args[idx + 1 :]

    flags = GrepFlags(
        recursive=recursive or bool(paths),
        ignore_case=ignore_case,
        line_numbers=line_numbers,
        files_with_matches=files_with_matches,
        regex=regex,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        limit=1000,
    )
    return ParsedGrep(pattern=pattern, paths=paths, flags=flags)


def _local_grep(stdin: str, pattern: str, flags: GrepFlags) -> str:
    if flags.regex:
        compiled = __import__("re").compile(pattern, __import__("re").IGNORECASE if flags.ignore_case else 0)
        matcher = lambda line: compiled.search(line) is not None
    elif flags.ignore_case:
        lowered = pattern.lower()
        matcher = lambda line: lowered in line.lower()
    else:
        matcher = lambda line: pattern in line

    out: list[str] = []
    for idx, line in enumerate(stdin.splitlines(), start=1):
        if matcher(line):
            out.append(f"{idx}:{line}" if flags.line_numbers else line)
    return "\n".join(out) + ("\n" if out else "")
