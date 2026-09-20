"""Unit tests for PathBuilder, transfer containment, and ScriptRegistry checksum."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from collection.services.paths import PathBuilder, safe_segment


UMP_ROOTS = {
    'UMP_STORAGE_ROOT': tempfile.mkdtemp(),
    'UMP_INPUT_ROOT': tempfile.mkdtemp(),
    'UMP_OUTPUT_ROOT': tempfile.mkdtemp(),
    'UMP_ARCHIVE_ROOT': tempfile.mkdtemp(),
    'UMP_PROCESSING_ROOT': tempfile.mkdtemp(),
    'UMP_ERROR_ROOT': tempfile.mkdtemp(),
    'UMP_QUARANTINE_ROOT': tempfile.mkdtemp(),
}


class SafeSegmentTests(TestCase):
    def test_normal_segment(self):
        self.assertEqual(safe_segment('orange', field='test'), 'orange')

    def test_dot_dot_rejected(self):
        with self.assertRaises(ValueError):
            safe_segment('..', field='test')

    def test_slash_rejected(self):
        with self.assertRaises(ValueError):
            safe_segment('a/b', field='test')

    def test_empty_falls_back(self):
        self.assertEqual(safe_segment('', field='test'), 'unknown')

    def test_none_falls_back(self):
        self.assertEqual(safe_segment(None, field='test'), 'unknown')


@override_settings(**UMP_ROOTS)
class PathBuilderInputTests(TestCase):
    def test_input_published_path(self):
        p = PathBuilder.input_published('orange', 'msc')
        self.assertIn('orange', str(p))
        self.assertIn('msc', str(p))
        self.assertTrue(str(p).startswith(UMP_ROOTS['UMP_INPUT_ROOT']))

    def test_input_staging_path(self):
        p = PathBuilder.input_staging('orange', 'msc')
        self.assertIn('staging', str(p))

    def test_input_archive_with_cbs_substream(self):
        p = PathBuilder.input_archive('orange', 'cbs', cbs_substream='voice')
        self.assertIn('voice', str(p))

    def test_stream_path_with_cbs(self):
        parts = PathBuilder.stream_path('orange', 'cbs', 'recharge')
        self.assertIn('recharge', parts)


@override_settings(**UMP_ROOTS)
class PathBuilderOutputTests(TestCase):
    def test_output_archive_path(self):
        p = PathBuilder.output_archive('bigdata', 'orange', 'msc')
        self.assertIn('bigdata', str(p))
        self.assertIn('orange', str(p))

    def test_output_archive_with_cbs(self):
        p = PathBuilder.output_archive('ipacs', 'orange', 'cbs', cbs_substream='data')
        self.assertIn('data', str(p))


@override_settings(**UMP_ROOTS)
class PathBuilderLifecycleTests(TestCase):
    def test_processing_path(self):
        p = PathBuilder.processing('orange', 'msc')
        self.assertTrue(str(p).startswith(UMP_ROOTS['UMP_PROCESSING_ROOT']))

    def test_error_input_path(self):
        p = PathBuilder.error_input('orange', 'msc')
        self.assertIn('input', str(p))

    def test_error_output_path(self):
        p = PathBuilder.error_output('bigdata', 'orange', 'msc')
        self.assertIn('output', str(p))
        self.assertIn('bigdata', str(p))

    def test_quarantine_path(self):
        p = PathBuilder.quarantine('orange', 'pgw')
        self.assertTrue(str(p).startswith(UMP_ROOTS['UMP_QUARANTINE_ROOT']))


@override_settings(**UMP_ROOTS)
class PathBuilderContainmentTests(TestCase):
    def test_validate_containment_inside(self):
        root = Path(UMP_ROOTS['UMP_INPUT_ROOT'])
        child = root / 'orange' / 'msc'
        self.assertTrue(PathBuilder.validate_containment(child, root))

    def test_validate_containment_outside(self):
        root = Path(UMP_ROOTS['UMP_INPUT_ROOT'])
        outside = Path('/tmp/evil')
        self.assertFalse(PathBuilder.validate_containment(outside, root))

    def test_validated_path_raises_on_escape(self):
        root = Path(UMP_ROOTS['UMP_INPUT_ROOT'])
        with self.assertRaises(ValueError):
            PathBuilder.validated_path(Path('/etc/passwd'), root)


@override_settings(**UMP_ROOTS, DATA_DIR=UMP_ROOTS['UMP_STORAGE_ROOT'])
class TransferContainmentTests(TestCase):
    def test_containment_rejects_outside_path(self):
        from collection.services import transfer
        transfer._UMP_ROOTS = None  # reset cache
        with self.assertRaises(ValueError):
            transfer._validate_containment(Path('/tmp/evil/dir'))

    def test_containment_accepts_inside_path(self):
        from collection.services import transfer
        transfer._UMP_ROOTS = None
        inside = Path(UMP_ROOTS['UMP_INPUT_ROOT']) / 'orange' / 'msc'
        transfer._validate_containment(inside)


class ScriptRegistryChecksumTests(TestCase):
    databases = {'default'}

    def test_compute_checksum(self):
        from scripts.models import ScriptRegistry
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write('print("hello")\n')
            f.flush()
            path = f.name
        try:
            digest = ScriptRegistry.compute_checksum(path)
            self.assertEqual(len(digest), 64)
            self.assertEqual(digest, ScriptRegistry.compute_checksum(path))
        finally:
            os.unlink(path)

    def test_verify_checksum_matches(self):
        from scripts.models import ScriptRegistry
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write('#!/bin/bash\necho ok\n')
            f.flush()
            path = f.name
        try:
            entry = ScriptRegistry(
                script_id='test', name='Test', script_type='SHELL',
                server_path=path, version='1.0',
                checksum=ScriptRegistry.compute_checksum(path),
            )
            self.assertTrue(entry.verify_checksum())
        finally:
            os.unlink(path)

    def test_verify_checksum_fails_on_tamper(self):
        from scripts.models import ScriptRegistry
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write('#!/bin/bash\necho ok\n')
            f.flush()
            path = f.name
        try:
            entry = ScriptRegistry(
                script_id='test', name='Test', script_type='SHELL',
                server_path=path, version='1.0',
                checksum='0' * 64,
            )
            self.assertFalse(entry.verify_checksum())
        finally:
            os.unlink(path)
