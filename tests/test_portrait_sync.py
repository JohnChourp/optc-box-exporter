import json
import tempfile
import unittest
from pathlib import Path

from optcbx.data import download_portraits

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _write_png_signature(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_SIGNATURE + b'test')


def _write_units(path: Path, count: int) -> None:
    units = [
        [f'Unit {index}', 'STR', ['Fighter'], 5]
        for index in range(1, count + 1)
    ]
    path.write_text(json.dumps(units))


class PortraitSyncTests(unittest.TestCase):
    def test_missing_source_url_becomes_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / 'Portraits'
            result = download_portraits._sync_single_portrait((
                {'id': 4204, 'sourceUrl': None, 'region': None, 'relativePath': None},
                output,
                None,
                'optc-db',
            ))

            self.assertEqual(result['action'], 'unresolved')
            self.assertFalse((output / '4204.png').exists())

    def test_team_builder_cache_path_supports_global_and_japan_packs(self) -> None:
        root = Path('/repo/optc-team-builder')

        global_path = download_portraits._team_builder_cache_path(root, {
            'region': 'glo',
            'relativePath': '5/000/5001.png',
        })
        japan_path = download_portraits._team_builder_cache_path(root, {
            'region': 'jap',
            'relativePath': '4/100/4121-1.png',
        })

        self.assertEqual(
            global_path,
            root / 'public' / 'assets' / 'offline-packs' / 'thumbnails-glo' / '5/000/5001.png',
        )
        self.assertEqual(
            japan_path,
            root / 'public' / 'assets' / 'offline-packs' / 'thumbnails-jap' / '4/100/4121-1.png',
        )

    def test_manual_override_is_copied_for_unresolved_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / 'optc-team-builder'
            output = Path(tmp_dir) / 'Portraits'
            manual_image = root / 'scripts' / 'data' / 'character-images' / '4204.png'
            overrides_path = root / 'scripts' / 'data' / 'character-image-overrides.json'
            _write_png_signature(manual_image)
            overrides_path.write_text(json.dumps({
                '4204': {
                    'source': 'manual',
                    'file': '4204.png',
                },
            }))

            items = download_portraits._apply_team_builder_overrides([
                {'id': 4204, 'sourceUrl': None, 'region': None, 'relativePath': None},
            ], root)
            result = download_portraits._sync_single_portrait((
                items[0],
                output,
                root,
                'optc-db',
            ))

            self.assertEqual(result['action'], 'copied')
            self.assertTrue(download_portraits._is_valid_png(output / '4204.png'))

    def test_portrait_status_allows_export_when_only_known_unresolved_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            units_path = root / 'units.json'
            portraits_path = root / 'Portraits'
            report_path = root / 'portrait-sync-report.json'
            _write_units(units_path, 2)
            _write_png_signature(portraits_path / '1.png')
            report_path.write_text(json.dumps({
                'viableCount': 2,
                'viableIdsHash': download_portraits._viable_ids_hash([1, 2]),
                'unresolvedIds': [2],
                'failedIds': [],
            }))

            before = download_portraits.build_local_portrait_status(
                units_path,
                portraits_path,
                report_path,
            )
            self.assertTrue(before['ready'])
            self.assertFalse(before['full_coverage_ready'])
            self.assertEqual(before['blocking_missing_ids'], [])
            self.assertEqual(before['unresolved_missing_ids'], [2])

            _write_png_signature(portraits_path / '2.png')
            report_path.write_text(json.dumps({
                'viableCount': 2,
                'viableIdsHash': download_portraits._viable_ids_hash([1, 2]),
                'unresolvedIds': [],
                'failedIds': [],
            }))
            after = download_portraits.build_local_portrait_status(
                units_path,
                portraits_path,
                report_path,
            )

            self.assertTrue(after['ready'])
            self.assertTrue(after['full_coverage_ready'])
            self.assertEqual(after['blocking_missing_count'], 0)
            self.assertEqual(after['unresolved_count'], 0)

    def test_portrait_status_blocks_missing_resolvable_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            units_path = root / 'units.json'
            portraits_path = root / 'Portraits'
            report_path = root / 'portrait-sync-report.json'
            _write_units(units_path, 2)
            _write_png_signature(portraits_path / '1.png')
            report_path.write_text(json.dumps({
                'viableCount': 2,
                'viableIdsHash': download_portraits._viable_ids_hash([1, 2]),
                'unresolvedIds': [],
                'failedIds': [],
            }))

            status = download_portraits.build_local_portrait_status(
                units_path,
                portraits_path,
                report_path,
            )

            self.assertFalse(status['ready'])
            self.assertFalse(status['full_coverage_ready'])
            self.assertEqual(status['blocking_missing_ids'], [2])
            self.assertEqual(status['unresolved_missing_ids'], [])


if __name__ == '__main__':
    unittest.main()
