from __future__ import annotations

import re
from contextvars import ContextVar

_PREFIX = re.compile(r"^/[A-Za-z0-9_-]+$")
_current_prefix: ContextVar[str] = ContextVar("govdeals_url_prefix", default="")


def prefix_from_header(value: str) -> str:
    raw = (value or "").strip().rstrip("/")
    if _PREFIX.fullmatch(raw):
        return raw
    return ""


def current_prefix() -> str:
    return _current_prefix.get()


def set_current_prefix(prefix: str):
    return _current_prefix.set(prefix)


def reset_current_prefix(token) -> None:
    _current_prefix.reset(token)


def public_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    prefix = current_prefix()
    if not prefix or path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix + path


def with_public_prefix(images):
    if not images:
        return images
    prefix = current_prefix()
    if not prefix:
        return images
    rewritten = []
    for image in images:
        item = dict(image)
        url = item.get("url") or ""
        if url.startswith("/") and not url.startswith(prefix + "/"):
            item["url"] = prefix + url
        rewritten.append(item)
    return rewritten
