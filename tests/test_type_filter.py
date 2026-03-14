import base64
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import optcbx.matcher as matcher
from PIL import Image

from optcbx import NoMatchingPortraitCandidatesError
from optcbx.app_flask import app
from optcbx.units import Character


def _sample_image_b64() -> str:
    image = Image.new('RGB', (4, 4), color=(255, 0, 0))
    buffered = io.BytesIO()
    image.save(buffered, format='PNG')
    return base64.b64encode(buffered.getvalue()).decode()


class MatcherTypeFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_portraits_paths = matcher._portraits_paths
        self._original_portraits = matcher._portraits
        self._original_valid_portraits_paths = matcher._valid_portraits_paths
        self._original_portrait_ids = matcher._portrait_ids
        self._original_units = matcher._units
        self._original_units_by_id = matcher._units_by_id
        self._original_unit_types_by_id = matcher._unit_types_by_id
        self._original_unit_classes_by_id = matcher._unit_classes_by_id

        matcher._portraits_paths = []
        matcher._portraits = {}
        matcher._valid_portraits_paths = {}
        matcher._portrait_ids = {}
        matcher._units = []
        matcher._units_by_id = {
            101: Character('STR unit', 'STR', ['Fighter'], '6', 101),
            202: Character('Dual unit', ['DEX', 'QCK'], ['Driven', 'Powerhouse'], '6', 202),
            303: Character('PSY unit', 'PSY', ['Shooter'], '6', 303),
        }
        matcher._unit_types_by_id = {
            101: frozenset({'STR'}),
            202: frozenset({'DEX', 'QCK'}),
            303: frozenset({'PSY'}),
        }
        matcher._unit_classes_by_id = {
            101: frozenset({'Fighter'}),
            202: frozenset({'Driven', 'Powerhouse'}),
            303: frozenset({'Shooter'}),
        }

        self.image_size = (64, 64)
        matcher._portraits[self.image_size] = np.zeros((3, 64, 64, 3), dtype='uint8')
        matcher._valid_portraits_paths[self.image_size] = [
            Path('data/Portraits/101.png'),
            Path('data/Portraits/202.png'),
            Path('data/Portraits/303.png'),
        ]
        matcher._portrait_ids[self.image_size] = np.array([101, 202, 303], dtype='int32')
        self.characters = np.zeros((1, 64, 64, 3), dtype='uint8')

    def tearDown(self) -> None:
        matcher._portraits_paths = self._original_portraits_paths
        matcher._portraits = self._original_portraits
        matcher._valid_portraits_paths = self._original_valid_portraits_paths
        matcher._portrait_ids = self._original_portrait_ids
        matcher._units = self._original_units
        matcher._units_by_id = self._original_units_by_id
        matcher._unit_types_by_id = self._original_unit_types_by_id
        matcher._unit_classes_by_id = self._original_unit_classes_by_id

    def test_normalize_allowed_types_dedupes_and_uppercases(self) -> None:
        normalized = matcher.normalize_allowed_types(['str', 'DEX', 'str', '  qck '])
        self.assertEqual(normalized, ('STR', 'DEX', 'QCK'))

    def test_normalize_allowed_classes_dedupes_and_canonicalizes(self) -> None:
        normalized = matcher.normalize_allowed_classes(['fighter', ' Free Spirit ', 'fighter'])
        self.assertEqual(normalized, ('Fighter', 'Free Spirit'))

    def test_find_characters_ids_keeps_full_candidate_pool_without_filter(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([1], None)) as mocked_top:
            ids = matcher.find_characters_ids(self.characters)

        self.assertEqual(ids, [202])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 3)
        self.assertIsNone(mocked_top.call_args.kwargs['portrait_indices'])

    def test_find_characters_ids_prunes_single_type_candidates(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([0], None)) as mocked_top:
            ids = matcher.find_characters_ids(self.characters, allowed_types=['str'])

        self.assertEqual(ids, [101])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 1)
        np.testing.assert_array_equal(
            mocked_top.call_args.kwargs['portrait_indices'],
            np.array([0], dtype='int32'),
        )

    def test_find_characters_ids_prunes_multiple_types(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([1], None)) as mocked_top:
            ids = matcher.find_characters_ids(self.characters, allowed_types=['DEX', 'PSY'])

        self.assertEqual(ids, [303])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 2)
        np.testing.assert_array_equal(
            mocked_top.call_args.kwargs['portrait_indices'],
            np.array([1, 2], dtype='int32'),
        )

    def test_find_characters_ids_keeps_dual_type_units_on_overlap(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([0], None)):
            ids = matcher.find_characters_ids(self.characters, allowed_types=['QCK'])

        self.assertEqual(ids, [202])

    def test_find_characters_ids_prunes_single_class_candidates(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([0], None)) as mocked_top:
            ids = matcher.find_characters_ids(self.characters, allowed_classes=['fighter'])

        self.assertEqual(ids, [101])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 1)
        np.testing.assert_array_equal(
            mocked_top.call_args.kwargs['portrait_indices'],
            np.array([0], dtype='int32'),
        )

    def test_find_characters_ids_prunes_multiple_classes(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([1], None)) as mocked_top:
            ids = matcher.find_characters_ids(
                self.characters,
                allowed_classes=['Powerhouse', 'Shooter'],
            )

        self.assertEqual(ids, [303])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 2)
        np.testing.assert_array_equal(
            mocked_top.call_args.kwargs['portrait_indices'],
            np.array([1, 2], dtype='int32'),
        )

    def test_find_characters_ids_keeps_dual_class_units_on_overlap(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([0], None)):
            ids = matcher.find_characters_ids(self.characters, allowed_classes=['powerhouse'])

        self.assertEqual(ids, [202])

    def test_find_characters_ids_combines_type_and_class_filters_with_intersection(self) -> None:
        with patch('optcbx.matcher._top_similarities', return_value=([0], None)) as mocked_top:
            ids = matcher.find_characters_ids(
                self.characters,
                allowed_types=['DEX', 'PSY'],
                allowed_classes=['Powerhouse'],
            )

        self.assertEqual(ids, [202])
        self.assertEqual(mocked_top.call_args.args[1].shape[0], 1)
        np.testing.assert_array_equal(
            mocked_top.call_args.kwargs['portrait_indices'],
            np.array([1], dtype='int32'),
        )

    def test_find_characters_ids_rejects_invalid_type_values(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Unsupported types filter values'):
            matcher.find_characters_ids(self.characters, allowed_types=['STR', 'RAINBOW'])

    def test_find_characters_ids_rejects_invalid_class_values(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Unsupported classes filter values'):
            matcher.find_characters_ids(self.characters, allowed_classes=['Fighter', 'Pirate King'])

    def test_find_characters_ids_raises_when_filter_removes_all_candidates(self) -> None:
        with self.assertRaisesRegex(NoMatchingPortraitCandidatesError, 'No portrait candidates remain'):
            matcher.find_characters_ids(self.characters, allowed_types=['INT'])

    def test_find_characters_ids_raises_when_class_filter_removes_all_candidates(self) -> None:
        with self.assertRaisesRegex(NoMatchingPortraitCandidatesError, 'No portrait candidates remain'):
            matcher.find_characters_ids(self.characters, allowed_classes=['Evolver'])


class ExportRouteTypeFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        app.testing = True
        self.client = app.test_client()
        self.image_b64 = _sample_image_b64()

    def test_export_passes_normalized_types_to_matcher(self) -> None:
        runtime = {'web_ready': True}
        characters = [Character('Dual unit', ['DEX', 'QCK'], ['Driven'], '6', 202)]
        thumbnails = np.zeros((1, 4, 4, 3), dtype='uint8')

        with patch('optcbx.app_flask._build_runtime_status', return_value=runtime), \
                patch('optcbx.app_flask.optcbx.find_characters_from_screenshot',
                      return_value=(characters, thumbnails)) as mocked_export:
            response = self.client.post('/export', json={
                'image': self.image_b64,
                'imageSize': 64,
                'returnThumbnails': True,
                'types': ['str', 'DEX', 'str'],
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['appliedTypes'], ['STR', 'DEX'])
        self.assertEqual(payload['characters'][0]['number'], 202)
        self.assertEqual(
            mocked_export.call_args.kwargs['allowed_types'],
            ('STR', 'DEX'),
        )
        self.assertEqual(
            mocked_export.call_args.kwargs['allowed_classes'],
            (),
        )

    def test_export_passes_normalized_classes_to_matcher(self) -> None:
        runtime = {'web_ready': True}
        characters = [Character('Fighter unit', 'STR', ['Fighter'], '6', 101)]
        thumbnails = np.zeros((1, 4, 4, 3), dtype='uint8')

        with patch('optcbx.app_flask._build_runtime_status', return_value=runtime), \
                patch('optcbx.app_flask.optcbx.find_characters_from_screenshot',
                      return_value=(characters, thumbnails)) as mocked_export:
            response = self.client.post('/export', json={
                'image': self.image_b64,
                'imageSize': 64,
                'returnThumbnails': True,
                'types': ['str'],
                'classes': ['fighter', ' free spirit ', 'fighter'],
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['appliedTypes'], ['STR'])
        self.assertEqual(payload['appliedClasses'], ['Fighter', 'Free Spirit'])
        self.assertEqual(
            mocked_export.call_args.kwargs['allowed_types'],
            ('STR',),
        )
        self.assertEqual(
            mocked_export.call_args.kwargs['allowed_classes'],
            ('Fighter', 'Free Spirit'),
        )

    def test_export_rejects_invalid_types_payload(self) -> None:
        response = self.client.post('/export', json={
            'image': self.image_b64,
            'imageSize': 64,
            'returnThumbnails': True,
            'types': ['RAINBOW'],
        })

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn('Unsupported types filter values', payload['message'])
        self.assertEqual(payload['appliedTypes'], [])
        self.assertEqual(payload['appliedClasses'], [])

    def test_export_rejects_invalid_classes_payload(self) -> None:
        response = self.client.post('/export', json={
            'image': self.image_b64,
            'imageSize': 64,
            'returnThumbnails': True,
            'types': ['STR'],
            'classes': ['Pirate King'],
        })

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn('Unsupported classes filter values', payload['message'])
        self.assertEqual(payload['appliedTypes'], ['STR'])
        self.assertEqual(payload['appliedClasses'], [])

    def test_export_returns_422_when_type_filter_is_too_restrictive(self) -> None:
        runtime = {'web_ready': True}

        with patch('optcbx.app_flask._build_runtime_status', return_value=runtime), \
                patch('optcbx.app_flask.optcbx.find_characters_from_screenshot',
                      side_effect=NoMatchingPortraitCandidatesError(
                          'No portrait candidates remain after applying the type filter (INT). Expand or clear the filter and retry.'
                      )):
            response = self.client.post('/export', json={
                'image': self.image_b64,
                'imageSize': 64,
                'returnThumbnails': True,
                'types': ['INT'],
            })

        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertIn('No portrait candidates remain', payload['message'])
        self.assertEqual(payload['appliedTypes'], ['INT'])

    def test_export_returns_422_when_combined_filter_is_too_restrictive(self) -> None:
        runtime = {'web_ready': True}

        with patch('optcbx.app_flask._build_runtime_status', return_value=runtime), \
                patch('optcbx.app_flask.optcbx.find_characters_from_screenshot',
                      side_effect=NoMatchingPortraitCandidatesError(
                          'No portrait candidates remain after applying the active filters (types: STR; classes: Shooter). Expand or clear the filter and retry.'
                      )):
            response = self.client.post('/export', json={
                'image': self.image_b64,
                'imageSize': 64,
                'returnThumbnails': True,
                'types': ['STR'],
                'classes': ['Shooter'],
            })

        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertIn('No portrait candidates remain', payload['message'])
        self.assertEqual(payload['appliedTypes'], ['STR'])
        self.assertEqual(payload['appliedClasses'], ['Shooter'])


if __name__ == '__main__':
    unittest.main()
