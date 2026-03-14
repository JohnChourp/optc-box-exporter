import json
import tempfile
import unittest
from pathlib import Path

from optcbx.audit import (EXPECTED_FILE_NAME, build_actual_export_payload,
                          build_audit_report, discover_case_image,
                          load_case_context, run_audit_case)
from optcbx.audit import _load_units_by_id  # noqa: PLC2701
from optcbx.units import Character


class AuditCaseContractTests(unittest.TestCase):
    def test_discover_case_image_requires_exactly_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)
            (case_dir / 'box-01.png').touch()
            (case_dir / 'box-02.jpg').touch()
            (case_dir / EXPECTED_FILE_NAME).write_text(json.dumps({
                'characters': [{'number': 2035, 'name': 'Buggy the Genius Jester'}],
            }))

            with self.assertRaisesRegex(ValueError, 'Expected exactly one screenshot image'):
                discover_case_image(case_dir)

    def test_load_case_context_preserves_duplicate_ids_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            case_dir = Path(tmp_dir)
            (case_dir / 'box.png').touch()
            (case_dir / EXPECTED_FILE_NAME).write_text(json.dumps({
                'caseId': 'duplicate-case',
                'characters': [
                    {'number': 2035, 'name': 'Buggy the Genius Jester'},
                    {'number': 2035, 'name': 'Buggy the Genius Jester'},
                    {'number': 1881, 'name': 'Hawk Eyes Mihawk - Black Blade: Night'},
                ],
            }))

            context = load_case_context(case_dir)

            self.assertEqual(context['caseId'], 'duplicate-case')
            self.assertEqual(
                [entry['number'] for entry in context['expectedCharacters']],
                [2035, 2035, 1881],
            )

    def test_build_actual_export_payload_keeps_browser_compatible_shape(self) -> None:
        payload = build_actual_export_payload('case-1', 64, [
            Character(
                name='Buggy the Genius Jester',
                type_='INT',
                class_=['Slasher'],
                stars='6',
                number=2035,
            ),
        ])

        self.assertEqual(payload['caseId'], 'case-1')
        self.assertEqual(payload['imageSize'], 64)
        self.assertEqual(payload['characters'][0]['number'], 2035)
        self.assertEqual(payload['characters'][0]['name'], 'Buggy the Genius Jester')


class AuditReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.units_by_id = _load_units_by_id()

    def test_report_marks_dataset_present_wrong_match(self) -> None:
        report = build_audit_report({
            'caseId': 'dataset-present',
            'imagePath': 'case.png',
            'notes': '',
            'expectedCharacters': [
                {'number': 2181, 'name': self.units_by_id[2181].name, 'position': 0},
            ],
        }, [
            self.units_by_id[606],
        ], [{
            'topCandidates': [{'number': 606}],
        }], self.units_by_id)

        self.assertEqual(report['summary']['status'], 'mismatch')
        self.assertEqual(report['summary']['mismatchCategories'], ['wrong_match_dataset_present'])

    def test_report_marks_missing_local_unit(self) -> None:
        report = build_audit_report({
            'caseId': 'missing-unit',
            'imagePath': 'case.png',
            'notes': '',
            'expectedCharacters': [
                {'number': 999999, 'name': 'Missing Local Unit', 'position': 0},
            ],
        }, [
            self.units_by_id[606],
        ], [{
            'topCandidates': [{'number': 606}],
        }], self.units_by_id)

        self.assertEqual(report['summary']['mismatchCategories'], ['expected_id_missing_from_local_units'])

    def test_report_marks_missing_portrait_asset(self) -> None:
        report = build_audit_report({
            'caseId': 'missing-portrait',
            'imagePath': 'case.png',
            'notes': '',
            'expectedCharacters': [
                {'number': 4204, 'name': self.units_by_id[4204].name, 'position': 0},
            ],
        }, [
            self.units_by_id[606],
        ], [{
            'topCandidates': [{'number': 606}],
        }], self.units_by_id)

        self.assertEqual(report['summary']['mismatchCategories'], ['missing_portrait_asset'])

    def test_report_marks_detection_count_mismatch(self) -> None:
        report = build_audit_report({
            'caseId': 'detection-mismatch',
            'imagePath': 'case.png',
            'notes': '',
            'expectedCharacters': [
                {'number': 2035, 'name': self.units_by_id[2035].name, 'position': 0},
                {'number': 2181, 'name': self.units_by_id[2181].name, 'position': 1},
            ],
        }, [
            self.units_by_id[2035],
        ], [{
            'topCandidates': [{'number': 2035}],
        }], self.units_by_id)

        self.assertEqual(report['summary']['mismatchCategories'], ['detection_count_mismatch'])


class SavedCaseRegressionTests(unittest.TestCase):
    def test_saved_cases_match_expected_when_fixtures_exist(self) -> None:
        fixtures_root = Path(__file__).resolve().parent / 'fixtures' / 'optcbx_cases'
        case_dirs = sorted(path for path in fixtures_root.iterdir() if path.is_dir()) if fixtures_root.exists() else []

        if not case_dirs:
            self.skipTest('No saved OPTCbx case folders yet.')

        for case_dir in case_dirs:
            with self.subTest(case=case_dir.name):
                report = run_audit_case(case_dir, image_size=64, write_artifacts=False)
                self.assertEqual(report['summary']['status'], 'exact_match')


if __name__ == '__main__':
    unittest.main()
