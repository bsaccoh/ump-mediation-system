from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from collection.services.paths import PathBuilder
from collection.services.transfer import publish_bytes, publish_file


class CanonicalPathTests(SimpleTestCase):
    def test_cbs_paths_include_substream_and_hour_partition(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with override_settings(
                UMP_INPUT_ROOT=root / 'landing' / 'input',
                UMP_OUTPUT_ROOT=root / 'landing' / 'output',
                UMP_ARCHIVE_ROOT=root / 'archive',
            ):
                self.assertEqual(
                    PathBuilder.input_staging('orange', 'cbs', 'voice'),
                    root / 'landing' / 'input' / 'orange' / 'cbs' / 'voice' / 'staging',
                )
                self.assertEqual(
                    PathBuilder.output_published('billing', 'orange', 'cbs', 'sms'),
                    root / 'landing' / 'output' / 'billing' / 'orange' / 'cbs' / 'sms',
                )

    def test_path_segments_reject_traversal(self):
        with self.assertRaises(ValueError):
            PathBuilder.input_published('../orange', 'msc')
        with self.assertRaises(ValueError):
            PathBuilder.output_published('billing', 'orange', 'cbs', '../sms')


class PublicationTests(SimpleTestCase):
    def test_generated_payload_is_published_only_after_staging(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / 'staging'
            published = root / 'published'
            destination = publish_bytes(b'cdr-data', staging, published, 'cdr.csv')
            self.assertEqual(destination.read_bytes(), b'cdr-data')
            self.assertEqual(list(staging.iterdir()), [])

    def test_verified_archive_transfer_removes_source_after_publish(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.dat'
            source.write_bytes(b'original-cdr')
            destination = publish_file(source, root / 'archive' / 'staging', root / 'archive', remove_source=True)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b'original-cdr')

    def test_publication_rejects_path_traversal_filename(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                publish_bytes(b'cdr', root / 'staging', root / 'published', '../cdr.csv')
