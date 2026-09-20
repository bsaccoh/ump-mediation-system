"""Controlled execution for reviewed server-side script registry entries."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied


def _roles_for(user) -> set[str]:
    roles = set()
    if user.is_superuser:
        roles.add('admin')
    if user.is_staff:
        roles.add('staff')
    for role in ('operator', 'analyst', 'auditor', 'regulator', 'regulatory_admin'):
        if getattr(user, f'is_{role}', False):
            roles.add(role)
    return roles


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as script_file:
        for block in iter(lambda: script_file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run_registered_script(registry_entry, user) -> tuple[str, str, int]:
    """Run a registry-approved script with fixed executables and no arguments."""
    if not registry_entry.enabled:
        raise PermissionDenied('The approved script is disabled.')
    allowed_roles = set(registry_entry.allowed_roles or [])
    if allowed_roles and not allowed_roles.intersection(_roles_for(user)):
        raise PermissionDenied('You are not authorized to execute this script.')

    root = Path(settings.UMP_APPROVED_SCRIPTS_ROOT).resolve()
    path = Path(registry_entry.server_path).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionDenied('The script is outside the approved scripts directory.') from exc
    if not path.is_file() or _checksum(path).lower() != registry_entry.checksum.lower():
        raise PermissionDenied('The approved script integrity check failed.')

    if registry_entry.script_type == registry_entry.ScriptType.PYTHON:
        command = [sys.executable, str(path)]
    elif registry_entry.script_type == registry_entry.ScriptType.SHELL and os.name != 'nt':
        command = ['/bin/sh', str(path)]
    else:
        raise PermissionDenied('This script type is not executable on this host.')

    result = subprocess.run(
        command,
        cwd=str(root),
        env={'PATH': os.environ.get('PATH', ''), 'PYTHONIOENCODING': 'utf-8'},
        text=True,
        capture_output=True,
        timeout=settings.UMP_SCRIPT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Approved script failed')[:4000])
    return result.stdout[:10000], result.stderr[:10000], result.returncode
