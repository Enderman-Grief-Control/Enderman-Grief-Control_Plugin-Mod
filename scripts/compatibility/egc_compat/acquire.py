"""Official download acquisition with a content-addressed, hash-verified cache."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CHUNK = 1024 * 1024
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


class AcquisitionError(Exception):
    def __init__(self, message: str, kind: str = "download"):
        super().__init__(message)
        self.kind = kind  # download | hash_mismatch | size_mismatch | missing_local | unsafe_path | deadline


def contained(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and refuse any result outside it.

    Checked on resolved paths, so traversal, absolute or drive-qualified parts, and symlink or
    junction escapes are all rejected before anything is read, written, or deleted.
    """
    root = base.resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise AcquisitionError(f"refusing path {'/'.join(parts)!r}: it resolves outside {root}", "unsafe_path")
    return candidate


@dataclass(frozen=True)
class HttpLimits:
    connect_s: float = 15.0
    read_s: float = 60.0
    retries: int = 2


def fs_path(path: Path) -> Path:
    """An extended-length path on Windows, so deep server library trees exceed MAX_PATH safely."""
    if os.name != "nt":
        return path
    text = str(path.resolve())
    return path if text.startswith("\\\\?\\") else Path("\\\\?\\" + text)


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with fs_path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _open(url: str, user_agent: str, limits: HttpLimits) -> http.client.HTTPResponse:
    for _redirect in range(6):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise AcquisitionError(f"refusing non-https URL {url}")
        connection = http.client.HTTPSConnection(parts.netloc, timeout=limits.connect_s)
        connection.connect()
        connection.sock.settimeout(limits.read_s)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path or "/", headers={"User-Agent": user_agent, "Accept": "*/*"})
        response = connection.getresponse()
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            response.close()
            connection.close()
            if not location:
                raise AcquisitionError(f"redirect without Location from {url}")
            url = urllib.parse.urljoin(url, location)
            continue
        if response.status != 200:
            body = response.read(300).decode("utf-8", "replace")
            connection.close()
            raise AcquisitionError(f"HTTP {response.status} from {url}: {body}")
        return response
    raise AcquisitionError(f"too many redirects from {url}")


MAX_JSON_BYTES = 32 * 1024 * 1024


def _check_deadline(deadline: float | None, describe: str) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise AcquisitionError(f"{describe} exceeded the row deadline", "deadline")


def _with_retries(action: Callable[[], Any], limits: HttpLimits, describe: str,
                  deadline: float | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(limits.retries + 1):
        _check_deadline(deadline, describe)
        try:
            return action()
        except AcquisitionError as error:
            if error.kind != "download":
                raise
            last = error
        except (OSError, http.client.HTTPException) as error:
            last = error
        if attempt < limits.retries:
            time.sleep(2 * (attempt + 1))
    raise AcquisitionError(f"{describe} failed after {limits.retries + 1} attempts: {last}")


def get_json(url: str, user_agent: str, limits: HttpLimits) -> Any:
    def action() -> Any:
        response = _open(url, user_agent, limits)
        try:
            body = response.read(MAX_JSON_BYTES + 1)
        finally:
            response.close()
        if len(body) > MAX_JSON_BYTES:
            raise AcquisitionError(f"{url} returned more than {MAX_JSON_BYTES} bytes", "size_mismatch")
        return json.loads(body.decode("utf-8"))
    return _with_retries(action, limits, f"GET {url}")


def download(url: str, destination: Path, user_agent: str, limits: HttpLimits, *,
             max_bytes: int | None = None, deadline: float | None = None) -> None:
    """Stream ``url`` to ``destination``, stopping at ``max_bytes`` or the absolute ``deadline``.

    Socket timeouts bound each read; the deadline bounds the whole transfer, so a peer that keeps
    trickling data cannot hold the run open.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    describe = f"download {url}"

    def action() -> None:
        response = _open(url, user_agent, limits)
        written = 0
        try:
            with partial.open("wb") as handle:
                for block in iter(lambda: response.read(CHUNK), b""):
                    written += len(block)
                    if max_bytes is not None and written > max_bytes:
                        raise AcquisitionError(f"{url} sent more than the pinned {max_bytes} bytes", "size_mismatch")
                    _check_deadline(deadline, describe)
                    handle.write(block)
        finally:
            response.close()
        os.replace(partial, destination)

    try:
        _with_retries(action, limits, describe, deadline)
    finally:
        if partial.exists():
            partial.unlink()


@dataclass(frozen=True)
class CachedFile:
    path: Path
    sha256: str
    size: int
    source: str
    from_cache: bool


def verify_file(path: Path, *, sha256: str | None = None, sha1: str | None = None,
                size: int | None = None) -> None:
    actual_size = path.stat().st_size
    if size is not None and actual_size != size:
        raise AcquisitionError(f"{path} is {actual_size} bytes, expected {size}", "size_mismatch")
    if sha256 is not None:
        actual = hash_file(path, "sha256")
        if actual != sha256:
            raise AcquisitionError(f"{path} SHA-256 {actual} does not match pinned {sha256}", "hash_mismatch")
    if sha1 is not None:
        actual = hash_file(path, "sha1")
        if actual != sha1:
            raise AcquisitionError(f"{path} SHA-1 {actual} does not match pinned {sha1}", "hash_mismatch")


def _cache_target(cache_dir: Path, filename: str, sha256: str | None, sha1: str | None) -> Path:
    if sha256 is not None and not SHA256_HEX.match(sha256):
        raise AcquisitionError(f"invalid pinned SHA-256 {sha256!r}", "unsafe_path")
    if sha1 is not None and not SHA1_HEX.match(sha1):
        raise AcquisitionError(f"invalid pinned SHA-1 {sha1!r}", "unsafe_path")
    key = f"sha256-{sha256}" if sha256 else f"sha1-{sha1}"
    return contained(cache_dir, "objects", key, filename)


def fetch_pinned(url: str, filename: str, cache_dir: Path, user_agent: str, limits: HttpLimits, *,
                 sha256: str | None = None, sha1: str | None = None, size: int | None = None,
                 deadline: float | None = None) -> CachedFile:
    """Return a verified cached copy of a pinned download, downloading it on a cache miss.

    The cache is keyed by the pinned hash, so a changed upstream file can never be served for an
    old pin: it fails verification instead. Every path is checked to stay inside the cache before
    anything is deleted or written.
    """
    if sha256 is None and sha1 is None:
        raise ValueError("a pinned download needs sha256 or sha1")
    target = _cache_target(cache_dir, filename, sha256, sha1)
    if target.is_file():
        try:
            verify_file(target, sha256=sha256, sha1=sha1, size=size)
            return CachedFile(target, sha256 or hash_file(target), target.stat().st_size, url, True)
        except AcquisitionError:
            target.unlink()
    download(url, target, user_agent, limits, max_bytes=size, deadline=deadline)
    try:
        verify_file(target, sha256=sha256, sha1=sha1, size=size)
    except AcquisitionError:
        os.replace(target, target.with_name(target.name + ".rejected"))
        raise
    return CachedFile(target, sha256 or hash_file(target), target.stat().st_size, url, False)


def copy_local_pinned(local_path: Path, filename: str, cache_dir: Path, *, sha256: str,
                      size: int) -> CachedFile:
    if not local_path.is_file():
        raise AcquisitionError(f"local artifact {local_path} does not exist", "missing_local")
    verify_file(local_path, sha256=sha256, size=size)
    target = _cache_target(cache_dir, filename, sha256, None)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)
    verify_file(target, sha256=sha256, size=size)
    return CachedFile(target, sha256, size, str(local_path), False)
