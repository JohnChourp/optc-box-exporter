import unittest

import numpy as np

from optcbx.square_detection import (
    _grid_lines_from_rectangles,
    _sort_detected_characters_by_grid,
    detect_characters_from_manual_grid,
    normalize_manual_grid,
)


def _build_character_tensor(ids):
    characters = np.zeros((len(ids), 2, 2, 3), dtype='uint8')
    for index, character_id in enumerate(ids):
        characters[index, ...] = character_id
    return characters


class GridOrderingTests(unittest.TestCase):
    def test_sort_uses_dynamic_characters_per_row(self) -> None:
        character_ids = [0, 1, 2, 3, 4, 5]
        characters = _build_character_tensor(character_ids)
        valid_rects = np.array([
            [20, 100, 10, 10],  # id 0, row 2 col 2
            [30, 10, 10, 10],   # id 1, row 1 col 3
            [10, 10, 10, 10],   # id 2, row 1 col 1
            [10, 100, 10, 10],  # id 3, row 2 col 1
            [20, 10, 10, 10],   # id 4, row 1 col 2
            [30, 100, 10, 10],  # id 5, row 2 col 3
        ], dtype='int32')

        sorted_characters, sorted_rects = _sort_detected_characters_by_grid(
            characters,
            valid_rects,
            characters_per_row=3,
        )

        ordered_ids = [int(slot[0, 0, 0]) for slot in sorted_characters]
        self.assertEqual(ordered_ids, [2, 4, 1, 3, 0, 5])
        self.assertEqual(sorted_rects.shape, (6, 4))

    def test_sort_defaults_to_legacy_five_columns(self) -> None:
        character_ids = [0, 1, 2, 3, 4, 5, 6]
        characters = _build_character_tensor(character_ids)
        valid_rects = np.array([
            [20, 10, 10, 10],   # id 0, row 1 col 2
            [30, 100, 10, 10],  # id 1, row 2 col 2
            [40, 10, 10, 10],   # id 2, row 1 col 4
            [10, 100, 10, 10],  # id 3, row 2 col 1
            [50, 10, 10, 10],   # id 4, row 1 col 5
            [10, 10, 10, 10],   # id 5, row 1 col 1
            [30, 10, 10, 10],   # id 6, row 1 col 3
        ], dtype='int32')

        default_sorted_characters, default_sorted_rects = _sort_detected_characters_by_grid(
            characters,
            valid_rects,
        )
        explicit_sorted_characters, explicit_sorted_rects = _sort_detected_characters_by_grid(
            characters,
            valid_rects,
            characters_per_row=5,
        )

        np.testing.assert_array_equal(default_sorted_characters, explicit_sorted_characters)
        np.testing.assert_array_equal(default_sorted_rects, explicit_sorted_rects)


class SplitGridTests(unittest.TestCase):
    def test_grid_lines_from_rectangles_merges_nearby_boundaries(self) -> None:
        rects = np.array([
            [10, 20, 30, 30],
            [41, 21, 30, 30],
            [10, 52, 30, 30],
            [42, 53, 30, 30],
        ], dtype='int32')

        vertical_lines, horizontal_lines = _grid_lines_from_rectangles(
            rects,
            (100, 100, 3),
        )

        self.assertEqual(vertical_lines, [10, 41, 72])
        self.assertEqual(horizontal_lines, [20, 52, 82])

    def test_normalize_manual_grid_rejects_invalid_lines(self) -> None:
        with self.assertRaisesRegex(ValueError, 'manualGrid.verticalLines'):
            normalize_manual_grid({
                'verticalLines': [0, 50, 101],
                'horizontalLines': [0, 50],
            }, (100, 100, 3))

        with self.assertRaisesRegex(ValueError, 'manualGrid.horizontalLines'):
            normalize_manual_grid({
                'verticalLines': [0, 50],
                'horizontalLines': [10],
            }, (100, 100, 3))

    def test_manual_grid_builds_row_major_crops(self) -> None:
        image = np.zeros((20, 20, 3), dtype='uint8')
        image[0:10, 0:10] = [10, 0, 0]
        image[0:10, 10:20] = [20, 0, 0]
        image[10:20, 0:10] = [30, 0, 0]
        image[10:20, 10:20] = [40, 0, 0]

        characters, rects = detect_characters_from_manual_grid(
            image,
            {
                'verticalLines': [20, 0, 10],
                'horizontalLines': [10, 0, 20],
            },
            (2, 2),
        )

        ordered_values = [int(character[0, 0, 0]) for character in characters]
        self.assertEqual(ordered_values, [10, 20, 30, 40])
        np.testing.assert_array_equal(
            rects,
            np.array([
                [0, 0, 10, 10],
                [10, 0, 10, 10],
                [0, 10, 10, 10],
                [10, 10, 10, 10],
            ], dtype='int32'),
        )


if __name__ == '__main__':
    unittest.main()
