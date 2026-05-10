from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Iterable


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".mdx",
    ".rst",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".html",
    ".css",
    ".xml",
    ".csv",
}


def normalize_virtual_path(path: str | None, cwd: str = "/") -> str:
    """Normalize a virtual POSIX path and prevent escaping above /."""
    if path is None or path == "":
        path = "."

    if not cwd.startswith("/"):
        cwd = "/" + cwd

    raw = path if path.startswith("/") else f"{cwd.rstrip('/')}/{path}"
    parts: list[str] = []

    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)

    return "/" + "/".join(parts) if parts else "/"


def parent_path(path: str) -> str:
    path = normalize_virtual_path(path)
    if path == "/":
        return "/"
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def basename(path: str) -> str:
    path = normalize_virtual_path(path)
    if path == "/":
        return ""
    return path.rsplit("/", 1)[-1]


def extension(path: str) -> str:
    name = basename(path)
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def depth(path: str) -> int:
    path = normalize_virtual_path(path)
    if path == "/":
        return 0
    return len([p for p in path.split("/") if p])


def path_prefixes(path: str) -> list[str]:
    """Return ancestor directory prefixes plus the path itself for directories.

    For files, callers usually pass the file path and receive ancestor directories.
    Example: /docs/auth/oauth.mdx -> [/, /docs, /docs/auth]
    Example directory: /docs/auth -> [/, /docs, /docs/auth]
    """
    path = normalize_virtual_path(path)
    if path == "/":
        return ["/"]

    parts = [p for p in path.split("/") if p]
    prefixes = ["/"]
    for idx in range(1, len(parts) + 1):
        prefixes.append("/" + "/".join(parts[:idx]))
    return prefixes


def ancestor_dir_prefixes_for_file(file_path: str) -> list[str]:
    """Return directory prefixes that contain a file, including /."""
    return path_prefixes(parent_path(file_path))


def path_hash(tenant_id: str, collection_id: str, path: str) -> str:
    normalized = normalize_virtual_path(path)
    value = f"{tenant_id}\0{collection_id}\0{normalized}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def content_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def guess_language(path: str) -> str | None:
    ext = extension(path)
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".md": "markdown",
        ".mdx": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".sql": "sql",
        ".sh": "shell",
        ".html": "html",
        ".css": "css",
    }
    if ext in mapping:
        return mapping[ext]
    mime, _ = mimetypes.guess_type(path)
    return mime


def is_probably_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        data = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def make_virtual_path_from_local(root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root).as_posix()
    return normalize_virtual_path("/" + rel)


def unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({v for v in values if v})
