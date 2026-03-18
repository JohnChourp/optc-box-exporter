import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from optcbx.audit import (CORRECTED_FILE_NAME, INPUT_DIR_NAME, META_DIR_NAME,
                          OUTPUT_DIR_NAME, build_audit_report,
                          build_favorites_consistency_checks,
                          build_track_report, discover_latest_favorites_json,
                          discover_output_images, discover_single_input_image,
                          load_case_context, write_case_artifacts)
from optcbx.audit import _load_units_by_id  # noqa: PLC2701


class AuditCaseContractTests(unittest.TestCase):
    def _create_case_dirs(self, case_dir: Path) -> tuple[Path, Path, Path]:
        input_dir = case_dir / INPUT_DIR_NAME
        output_dir = case_dir / OUTPUT_DIR_NAME
        meta_dir = case_dir / META_DIR_NAME
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        meta_dir.mkdir(parents=True)
        return input_dir, output_dir, meta_dir

    def _write_corrected(self, meta_dir: Path) -> None:
        (meta_dir / CORRECTED_FILE_NAME).write_text(json.dumps({
            'caseId': 'case-1',
            'characters': [
                {'number': 2035, 'name': 'Buggy the Genius Jester'},
                {'number': 1881, 'name': 'Hawk Eyes Mihawk - Black Blade: Night'},
            ],
        }))

    def test_load_case_context_requires_input_output_meta_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)

            with self.assertRaisesRegex(FileNotFoundError, 'Missing required directory'):
                load_case_context(case_dir)

    def test_discover_single_input_image_requires_exactly_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_dir = Path(tmp_dir)

            with self.assertRaisesRegex(ValueError, 'Expected exactly one input screenshot image'):
                discover_single_input_image(input_dir)

            (input_dir / 'box-01.png').touch()
            (input_dir / 'box-02.jpg').touch()

            with self.assertRaisesRegex(ValueError, 'Expected exactly one input screenshot image'):
                discover_single_input_image(input_dir)

    def test_discover_output_images_requires_at_least_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaisesRegex(ValueError, 'Expected at least one output character image'):
                discover_output_images(output_dir)

    def test_load_case_context_sorts_output_images_naturally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)
            input_dir, output_dir, meta_dir = self._create_case_dirs(case_dir)

            (input_dir / 'input.png').touch()
            (output_dir / 'slot-10.png').touch()
            (output_dir / 'slot-2.png').touch()
            (output_dir / 'slot-1.png').touch()
            self._write_corrected(meta_dir)

            context = load_case_context(case_dir)

            self.assertEqual(
                [path.name for path in context['outputImagePaths']],
                ['slot-1.png', 'slot-2.png', 'slot-10.png'],
            )

    def test_load_case_context_requires_corrected_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)
            input_dir, output_dir, meta_dir = self._create_case_dirs(case_dir)

            (input_dir / 'input.png').touch()
            (output_dir / 'slot-1.png').touch()

            with self.assertRaisesRegex(FileNotFoundError, f'Missing {META_DIR_NAME}/{CORRECTED_FILE_NAME}'):
                load_case_context(case_dir)

            (meta_dir / CORRECTED_FILE_NAME).write_text(json.dumps({'characters': []}))
            with self.assertRaisesRegex(ValueError, 'must contain a non-empty characters array'):
                load_case_context(case_dir)

    def test_favorites_autodetect_picks_latest_pattern_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_dir = Path(tmp_dir)
            older = meta_dir / 'favorites-old.json'
            newer = meta_dir / 'optcbx-favorites-20260318-220000.json'

            older.write_text(json.dumps({'characters': []}))
            time.sleep(0.01)
            newer.write_text(json.dumps({'characters': []}))

            found = discover_latest_favorites_json(meta_dir)
            self.assertEqual(found, newer)


class AuditReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.units_by_id = _load_units_by_id()

    def test_track_report_captures_mismatch_category(self) -> None:
        expected = [{'number': 2181, 'name': self.units_by_id[2181].name, 'position': 0}]
        actual = [{'number': 606, 'name': self.units_by_id[606].name}]
        diagnostics = [{'topCandidates': [{'number': 606}]}]

        track = build_track_report(expected, actual, diagnostics, self.units_by_id)

        self.assertEqual(track['summary']['status'], 'mismatch')
        self.assertEqual(track['summary']['mismatchCategories'], ['wrong_match_dataset_present'])

    def test_main_report_status_comes_from_current_track(self) -> None:
        current_track = {
            'summary': {
                'status': 'mismatch',
                'mismatchCategories': ['wrong_match_dataset_present'],
            },
            'comparisons': [],
        }
        provided_track = {
            'summary': {
                'status': 'exact_match',
                'mismatchCategories': [],
            },
            'comparisons': [],
        }

        report = build_audit_report({
            'caseId': 'case-1',
            'inputImagePath': Path('input/box.png'),
            'outputImagePaths': [Path('output/1.png')],
            'correctedPath': Path('meta/corrected.json'),
            'favoritesPath': None,
            'notes': '',
        }, current_track, provided_track, {'status': 'not_provided'})

        self.assertEqual(report['summary']['status'], 'mismatch')
        self.assertEqual(report['summary']['currentStatus'], 'mismatch')
        self.assertEqual(report['summary']['providedStatus'], 'exact_match')

    def test_favorites_consistency_checks_are_set_based(self) -> None:
        checks = build_favorites_consistency_checks(
            expected_entries=[
                {'number': 1, 'name': 'A', 'position': 0},
                {'number': 2, 'name': 'B', 'position': 1},
                {'number': 2, 'name': 'B', 'position': 2},
            ],
            favorites_entries=[
                {'number': 2, 'name': 'B'},
                {'number': 3, 'name': 'C'},
            ],
            favorites_path=Path('meta/optcbx-favorites.json'),
        )

        self.assertEqual(checks['status'], 'mismatch')
        self.assertEqual(checks['missingFromFavorites'], [1])
        self.assertEqual(checks['extraInFavorites'], [3])


class ArtifactGenerationTests(unittest.TestCase):
    def test_write_case_artifacts_outputs_all_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)

            track_stub = {
                'summary': {
                    'status': 'exact_match',
                    'expectedCount': 1,
                    'actualCount': 1,
                    'matchedCount': 1,
                    'mismatchCount': 0,
                    'mismatchCategories': [],
                },
                'comparisons': [{
                    'position': 0,
                    'status': 'matched',
                    'mismatchCategory': None,
                    'expected': {'number': 2035, 'name': 'Buggy the Genius Jester', 'position': 0},
                    'actual': {'number': 2035, 'name': 'Buggy the Genius Jester'},
                    'topCandidates': [],
                }],
            }

            report = {
                'caseId': 'case-1',
                'current': track_stub,
                'provided': track_stub,
            }
            thumbnails = np.zeros((1, 64, 64, 3), dtype=np.uint8)

            write_case_artifacts(
                case_folder=case_dir,
                current_export_payload={'caseId': 'case-1', 'imageSize': 64, 'source': 'current_rerun', 'characters': []},
                provided_export_payload={'caseId': 'case-1', 'imageSize': 64, 'source': 'provided_output_baseline', 'characters': []},
                report=report,
                current_thumbnails=thumbnails,
                provided_thumbnails=thumbnails,
            )

            self.assertTrue((case_dir / 'actual-export.json').exists())
            self.assertTrue((case_dir / 'provided-export.json').exists())
            self.assertTrue((case_dir / 'audit-report.json').exists())
            self.assertTrue((case_dir / 'actual-grid.html').exists())
            self.assertTrue((case_dir / 'provided-grid.html').exists())


if __name__ == '__main__':
    unittest.main()
