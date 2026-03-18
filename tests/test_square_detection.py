import unittest

import numpy as np

from optcbx.square_detection import _sort_detected_characters_by_grid


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


if __name__ == '__main__':
    unittest.main()
