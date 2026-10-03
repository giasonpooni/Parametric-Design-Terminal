"""Read-only provisioning checks shared by source-pinned integration gates."""
from __future__ import annotations

from hashlib import new as new_hash
import os
from pathlib import Path
import re
import subprocess


_CACHE_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class ProviderCheckoutError(ValueError):
    """A checkout refusal with a stable category for diagnostic callers."""

    def __init__(self, message: str, *, classification: str, code: str) -> None:
        super().__init__(message)
        self.classification = classification
        self.code = code


def _git(path: Path, *arguments: str) -> bytes:
    # Avoid even Git's optional index refresh when inspecting operator-owned
    # checkouts. Command-scoped configuration never changes their config files.
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        environment.pop(name, None)
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
             "-C", str(path), *arguments], check=True, capture_output=True, timeout=60,
            env=environment,
        ).stdout
    except FileNotFoundError as exc:
        raise ProviderCheckoutError(f"Cannot verify provider checkout: {path}",
            classification="dependency_unavailable", code="GIT_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderCheckoutError(f"Cannot verify provider checkout: {path}",
            classification="setup_failure", code="GIT_TIMEOUT") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderCheckoutError(f"Cannot verify provider checkout: {path}",
            classification="setup_failure", code="GIT_FAILED") from exc


def validate_checkout(path: Path, revision: str) -> Path:
    """Require the exact existing pin and tracked bytes, without checking out.

    Gitlinks remain separately owned boundaries: an uninitialized submodule is
    permitted; initialized submodules receive the same read-only checks. The
    operation-specific runtime still enforces its executable source allowlist.
    """
    return _validate_checkout(path, revision, set())


def validate_tracked_checkout(path: Path, revision: str) -> Path:
    """Check committed/index/working bytes without classifying untracked files.

    This narrow source preflight is useful before copying only committed Git
    objects. It never authorizes importing working files and does not replace
    strict provider validation, a monorepo import audit, or runtime qualification.
    Initialized gitlinks retain their original strict validation.
    """
    return _validate_checkout(path, revision, set(), reject_untracked=False)


def _validate_checkout(path: Path, revision: str, seen: set[Path], *, reject_untracked: bool = True) -> Path:
    path = Path(path).expanduser().resolve()
    if path in seen:
        raise ProviderCheckoutError(f"Provider gitlink forms a checkout cycle: {path}",
            classification="setup_failure", code="GITLINK_CYCLE")
    seen.add(path)
    if not path.is_dir():
        raise ProviderCheckoutError(f"Provider checkout is unavailable: {path}",
            classification="dependency_unavailable", code="CHECKOUT_UNAVAILABLE")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise ProviderCheckoutError("Provider revision must be a full lowercase commit ID",
            classification="setup_failure", code="INVALID_REVISION")
    if Path(os.fsdecode(_git(path, "rev-parse", "--show-toplevel")).strip()).resolve() != path:
        raise ProviderCheckoutError(f"Provider path must be a repository root: {path}",
            classification="identity_mismatch", code="NOT_REPOSITORY_ROOT")
    if _git(path, "rev-parse", "HEAD").decode().strip() != revision:
        raise ProviderCheckoutError(f"Provider checkout has the wrong pin; require {revision}: {path}",
            classification="identity_mismatch", code="WRONG_PIN")
    # Worktree comparisons in git status/diff may execute repository-configured
    # clean filters. Read only the index and committed tree metadata instead;
    # working bytes and executable modes are inspected below without Git filters.
    entries = []
    for entry in _git(path, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, expected = metadata.decode("ascii").split()
        if (mode, kind) not in {("100644", "blob"), ("100755", "blob"),
                                ("120000", "blob"), ("160000", "commit")}:
            raise ProviderCheckoutError(f"Unsupported provider tree entry: {os.fsdecode(raw_name)}",
                classification="setup_failure", code="UNSUPPORTED_TREE_ENTRY")
        entries.append((mode, kind, expected, raw_name))
    committed_index = sorted((raw_name, mode, expected, "0") for mode, _, expected, raw_name in entries)
    observed_index = []
    for entry in _git(path, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, expected, stage = metadata.decode("ascii").split()
            observed_index.append((raw_name, mode, expected, stage))
    if sorted(observed_index) != committed_index:
        raise ProviderCheckoutError(f"Provider checkout is dirty; index differs from its pin: {path}",
            classification="identity_mismatch", code="DIRTY_CHECKOUT")
    exclusions = [f":(exclude,glob)**/{name}/**" for name in sorted(_CACHE_DIRS)]
    if reject_untracked and _git(path, "ls-files", "--others", "-z", "--", ".", *exclusions):
        raise ProviderCheckoutError(f"Provider checkout contains untracked files outside runtime caches: {path}",
            classification="identity_mismatch", code="UNTRACKED_FILES")
    object_format = _git(path, "rev-parse", "--show-object-format").decode().strip()
    if object_format not in {"sha1", "sha256"}:
        raise ProviderCheckoutError(f"Unsupported Git object format: {object_format}",
            classification="setup_failure", code="UNSUPPORTED_OBJECT_FORMAT")
    for mode, kind, expected, raw_name in entries:
        name = os.fsdecode(raw_name)
        item = path / name
        if kind == "commit":
            if item.is_symlink() or (item.exists() and not item.is_dir()):
                raise ProviderCheckoutError(f"Provider gitlink changed type: {item}",
                    classification="identity_mismatch", code="GITLINK_TYPE_MISMATCH")
            if item.exists():
                if (item / ".git").exists():
                    _validate_checkout(item, expected, seen)
                elif any(item.iterdir()):
                    raise ProviderCheckoutError(f"Uninitialized provider gitlink is not empty: {item}",
                        classification="identity_mismatch", code="GITLINK_UNVERIFIED_CONTENT")
            continue
        try:
            if kind != "blob" or (mode != "120000" and item.is_symlink()) or (not item.is_symlink() and item.exists() and not item.is_file()):
                raise ProviderCheckoutError(f"Provider tracked file changed type: {item}",
                    classification="identity_mismatch", code="TRACKED_TYPE_MISMATCH")
            data = os.fsencode(os.readlink(item)) if item.is_symlink() else item.read_bytes()
            if mode != "120000" and os.name == "posix" and bool(item.stat().st_mode & 0o111) != (mode == "100755"):
                raise ProviderCheckoutError(f"Provider tracked executable mode differs from its pin: {item}",
                    classification="identity_mismatch", code="TRACKED_MODE_MISMATCH")
        except FileNotFoundError as exc:
            raise ProviderCheckoutError(f"Cannot read pinned provider file: {item}",
                classification="identity_mismatch", code="TRACKED_FILE_MISSING") from exc
        except OSError as exc:
            raise ProviderCheckoutError(f"Cannot read pinned provider file: {item}",
                classification="setup_failure", code="TRACKED_FILE_UNREADABLE") from exc
        actual = new_hash(object_format, b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if actual != expected:
            raise ProviderCheckoutError(f"Provider checkout is dirty; tracked bytes differ from their pin: {item}",
                classification="identity_mismatch", code="TRACKED_BYTES_MISMATCH")
    return path
