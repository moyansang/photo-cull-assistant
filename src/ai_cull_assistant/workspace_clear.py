"""Safely clear generated state from one selected workspace."""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterable


def _overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def clear_workspace(
    workspace: str | Path,
    *,
    input_dir: str | Path,
    program_dir: str | Path,
    original_paths: Iterable[str | Path] = (),
) -> None:
    """Remove every child of *workspace* after proving it cannot contain originals.

    The workspace directory itself remains available for the same project.  No
    symlink or junction is followed: such entries are unlinked as entries.
    """
    requested = Path(workspace)
    if requested.is_symlink() or (hasattr(requested, "is_junction") and requested.is_junction()):
        raise ValueError("工作区本身不能是快捷链接或目录联接")
    target = requested.resolve()
    source = Path(input_dir).resolve()
    program = Path(program_dir).resolve()
    if not target.is_dir():
        raise ValueError("当前工作区不存在")
    if _overlap(target, source):
        raise ValueError("工作区与照片文件夹重叠，为保护原照片不能清空")
    if target == program or target in program.parents:
        raise ValueError("工作区包含程序文件，为保护程序文件不能清空")
    if program in target.parents:
        relative = target.relative_to(program)
        if len(relative.parts) < 2 or relative.parts[0].casefold() != "workspaces":
            raise ValueError("程序文件夹内只允许清空 workspaces 下的单个工作区")
    for value in original_paths:
        original = Path(value).resolve()
        if _overlap(target, original):
            raise ValueError(f"工作区与原照片路径重叠，不能清空：{original}")

    for child in target.iterdir():
        if hasattr(child, "is_junction") and child.is_junction():
            child.rmdir()
        elif child.is_symlink():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
