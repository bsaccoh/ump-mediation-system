import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings

from scripts.services.execution import run_registered_script


class ApprovedScriptExecutionTests(SimpleTestCase):
    def _entry(self, path, checksum, *, roles=None):
        return SimpleNamespace(
            enabled=True,
            allowed_roles=roles or ['admin'],
            server_path=str(path),
            checksum=checksum,
            script_type='PYTHON',
            ScriptType=SimpleNamespace(PYTHON='PYTHON', SHELL='SHELL'),
        )

    def _admin(self):
        return SimpleNamespace(
            is_superuser=True, is_staff=True, is_operator=False, is_analyst=False,
            is_auditor=False, is_regulator=False, is_regulatory_admin=False,
        )

    def test_runs_verified_script_inside_approved_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'health.py'
            script.write_text("print('approved')\n", encoding='utf-8')
            checksum = hashlib.sha256(script.read_bytes()).hexdigest()
            with override_settings(UMP_APPROVED_SCRIPTS_ROOT=root):
                output, error, code = run_registered_script(self._entry(script, checksum), self._admin())
            self.assertEqual(output.strip(), 'approved')
            self.assertEqual(error, '')
            self.assertEqual(code, 0)

    def test_rejects_checksum_mismatch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'health.py'
            script.write_text("print('approved')\n", encoding='utf-8')
            with override_settings(UMP_APPROVED_SCRIPTS_ROOT=root):
                with self.assertRaises(PermissionDenied):
                    run_registered_script(self._entry(script, '0' * 64), self._admin())

    def test_rejects_script_outside_approved_root(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as other_directory:
            root = Path(directory)
            script = Path(other_directory) / 'outside.py'
            script.write_text("print('outside')\n", encoding='utf-8')
            checksum = hashlib.sha256(script.read_bytes()).hexdigest()
            with override_settings(UMP_APPROVED_SCRIPTS_ROOT=root):
                with self.assertRaises(PermissionDenied):
                    run_registered_script(self._entry(script, checksum), self._admin())
