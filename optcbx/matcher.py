import functools
import json
import multiprocessing as mp
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import tqdm.auto as tqdm

from optcbx.square_detection import detect_characters, trim_character_crop
from optcbx.units import Character, parse_units

SUPPORTED_TYPES = ('STR', 'DEX', 'QCK', 'PSY', 'INT')
DEFAULT_SUPPORTED_CLASSES = (
    'Booster',
    'Cerebral',
    'Driven',
    'Evolver',
    'Fighter',
    'Free Spirit',
    'Powerhouse',
    'Shooter',
    'Slasher',
    'Striker',
)
INVALID_CLASS_PATTERN = re.compile(r'^Class\d+$', re.IGNORECASE)


class NoMatchingPortraitCandidatesError(ValueError):
    """Raised when strict portrait filters remove every portrait candidate."""


# Keep computational expensive variables in memory
_portraits_paths: Optional[List[Path]] = None
_portraits: Dict[Tuple[int, int], np.ndarray] = {}
_valid_portraits_paths: Dict[Tuple[int, int], List[Path]] = {}
_portrait_ids: Dict[Tuple[int, int], np.ndarray] = {}
_units: Optional[List[Character]] = None
_units_by_id: Dict[int, Character] = {}
_unit_types_by_id: Dict[int, FrozenSet[str]] = {}
_unit_classes_by_id: Dict[int, FrozenSet[str]] = {}
_weight_maps: Dict[Tuple[int, int], np.ndarray] = {}
_inner_slices: Dict[Tuple[int, int], Tuple[slice, slice]] = {}

FindCharactersResult = Union[
    List[int],
    Tuple[List[int], np.ndarray],
    Tuple[List[int], List[Dict[str, Any]]],
    Tuple[List[int], np.ndarray, List[Dict[str, Any]]],
]


def _filter_value_key(value: str) -> str:
    return re.sub(r'\s+', ' ', value.strip()).casefold()


def _extract_unit_class_values(raw_class: Union[str, Sequence[str]]) -> Tuple[str, ...]:
    values = raw_class if isinstance(raw_class, (list, tuple, set, frozenset)) else [raw_class]
    normalized: List[str] = []
    seen = set()

    for value in values:
        if not isinstance(value, str):
            continue

        clean_value = re.sub(r'\s+', ' ', value.strip())
        if not clean_value or INVALID_CLASS_PATTERN.match(clean_value):
            continue

        value_key = _filter_value_key(clean_value)
        if value_key in seen:
            continue

        seen.add(value_key)
        normalized.append(clean_value)

    return tuple(normalized)


def _load_supported_classes() -> Tuple[str, ...]:
    try:
        with open('data/units.json') as units_file:
            raw_units = json.load(units_file)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return DEFAULT_SUPPORTED_CLASSES

    supported_classes: Dict[str, str] = {}
    for unit in parse_units(raw_units):
        for class_name in _extract_unit_class_values(unit.class_):
            supported_classes[_filter_value_key(class_name)] = class_name

    if not supported_classes:
        return DEFAULT_SUPPORTED_CLASSES

    return tuple(sorted(supported_classes.values(), key=lambda value: value.casefold()))


SUPPORTED_CLASSES = _load_supported_classes()
_supported_classes_by_key = {
    _filter_value_key(class_name): class_name
    for class_name in SUPPORTED_CLASSES
}


def find_characters_from_screenshot(
        screenshot: np.ndarray,
        image_size: Union[int, Tuple[int, int]] = 64,
        dist_method: str = 'mse',
        return_thumbnails: bool = False,
        return_diagnostics: bool = False,
        approach: str = 'smart',
        allowed_types: Optional[Sequence[str]] = None,
        allowed_classes: Optional[Sequence[str]] = None,
        characters_per_row: Optional[int] = None) -> Union[
            List[Character],
            Tuple[List[Character], np.ndarray],
            Tuple[List[Character], List[Dict[str, Any]]],
            Tuple[List[Character], np.ndarray, List[Dict[str, Any]]],
        ]:

    if isinstance(image_size, int):
        image_size = (image_size,) * 2

    characters = detect_characters(
        screenshot,
        image_size,
        approach=approach,
        characters_per_row=characters_per_row,
    )
    match_result = find_characters_ids(
        characters,
        dist_method=dist_method,
        return_diagnostics=return_diagnostics,
        allowed_types=allowed_types,
        allowed_classes=allowed_classes,
    )

    if return_diagnostics:
        id_matches = match_result[0]
        diagnostics = match_result[1]
    else:
        id_matches = match_result
        diagnostics = None

    _ensure_units_loaded()
    matched_units = [_units_by_id[i] for i in id_matches]

    if return_thumbnails and return_diagnostics:
        return matched_units, characters, diagnostics

    if return_thumbnails:
        return matched_units, characters

    if return_diagnostics:
        return matched_units, diagnostics

    return matched_units


def find_characters_ids(characters: np.ndarray,
                        return_portraits: bool = False,
                        dist_method: str = 'mse',
                        return_diagnostics: bool = False,
                        shortlist_size: int = 25,
                        rerank_size: int = 8,
                        diagnostics_count: int = 5,
                        allowed_types: Optional[Sequence[str]] = None,
                        allowed_classes: Optional[Sequence[str]] = None) -> FindCharactersResult:

    if isinstance(characters, list):
        characters = np.asarray(characters)

    if characters.size == 0:
        empty_ids: List[int] = []
        empty_diagnostics: List[Dict[str, Any]] = []

        if return_portraits and return_diagnostics:
            return empty_ids, np.empty((0, 0, 0, 3), dtype='uint8'), empty_diagnostics
        if return_portraits:
            return empty_ids, np.empty((0, 0, 0, 3), dtype='uint8')
        if return_diagnostics:
            return empty_ids, empty_diagnostics
        return empty_ids

    image_size = tuple(int(o) for o in characters.shape[1:3])
    normalized_allowed_types = normalize_allowed_types(allowed_types)
    normalized_allowed_classes = normalize_allowed_classes(allowed_classes)

    global _portraits, _portraits_paths
    global _portrait_ids, _valid_portraits_paths

    if _portraits_paths is None:
        _portraits_paths = sorted(Path('data/Portraits').glob('*.png'))

    if image_size not in _portraits:
        portraits = []
        valid_paths = []
        for path in tqdm.tqdm(_portraits_paths):
            im = _load_im(path, image_size)
            if im is None:
                continue
            portraits.append(im)
            valid_paths.append(path)

        _portraits[image_size] = np.array(portraits)
        _portrait_ids[image_size] = np.array(
            [int(path.stem) for path in valid_paths],
            dtype='int32',
        )
        _valid_portraits_paths[image_size] = valid_paths

    portraits = _portraits[image_size]
    valid_paths = _valid_portraits_paths[image_size]
    portrait_indices = None

    if normalized_allowed_types or normalized_allowed_classes:
        portrait_indices = _filter_portrait_indices(
            _portrait_ids[image_size],
            normalized_allowed_types,
            normalized_allowed_classes,
        )
        if portrait_indices.size == 0:
            raise NoMatchingPortraitCandidatesError(
                _build_no_matching_candidates_message(
                    normalized_allowed_types,
                    normalized_allowed_classes,
                )
            )

        portraits = portraits[portrait_indices]
        valid_paths = [valid_paths[index] for index in portrait_indices.tolist()]

    best_matches, diagnostics = _top_similarities(
        characters,
        portraits,
        method=dist_method,
        return_diagnostics=return_diagnostics,
        shortlist_size=shortlist_size,
        rerank_size=rerank_size,
        diagnostics_count=diagnostics_count,
        portrait_indices=portrait_indices,
    )

    ids = [int(valid_paths[i].stem) for i in best_matches]

    if diagnostics is not None:
        _decorate_diagnostics(diagnostics, valid_paths, ids)

    if return_portraits and return_diagnostics:
        return ids, portraits[best_matches], diagnostics
    if return_portraits:
        return ids, portraits[best_matches]
    if return_diagnostics:
        return ids, diagnostics
    return ids


def normalize_allowed_types(
        allowed_types: Optional[Sequence[str]]) -> Tuple[str, ...]:
    if allowed_types is None:
        return ()

    if isinstance(allowed_types, str):
        raw_values = [allowed_types]
    elif isinstance(allowed_types, (list, tuple, set, frozenset)):
        raw_values = list(allowed_types)
    else:
        raise ValueError("Types filter must be an array of strings.")

    normalized: List[str] = []
    invalid_values: List[str] = []
    seen = set()

    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            invalid_values.append(str(raw_value))
            continue

        value = raw_value.strip().upper()
        if not value:
            continue
        if value not in SUPPORTED_TYPES:
            invalid_values.append(raw_value)
            continue
        if value not in seen:
            seen.add(value)
            normalized.append(value)

    if invalid_values:
        supported_values = ', '.join(SUPPORTED_TYPES)
        invalid_list = ', '.join(str(value) for value in invalid_values)
        raise ValueError(
            f"Unsupported types filter values: {invalid_list}. "
            f"Supported values: {supported_values}."
        )

    return tuple(normalized)


def normalize_allowed_classes(
        allowed_classes: Optional[Sequence[str]]) -> Tuple[str, ...]:
    if allowed_classes is None:
        return ()

    if isinstance(allowed_classes, str):
        raw_values = [allowed_classes]
    elif isinstance(allowed_classes, (list, tuple, set, frozenset)):
        raw_values = list(allowed_classes)
    else:
        raise ValueError("Classes filter must be an array of strings.")

    normalized: List[str] = []
    invalid_values: List[str] = []
    seen = set()

    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            invalid_values.append(str(raw_value))
            continue

        value_key = _filter_value_key(raw_value)
        if not value_key:
            continue

        canonical_value = _supported_classes_by_key.get(value_key)
        if canonical_value is None:
            invalid_values.append(raw_value)
            continue

        if canonical_value not in seen:
            seen.add(canonical_value)
            normalized.append(canonical_value)

    if invalid_values:
        supported_values = ', '.join(SUPPORTED_CLASSES)
        invalid_list = ', '.join(str(value) for value in invalid_values)
        raise ValueError(
            f"Unsupported classes filter values: {invalid_list}. "
            f"Supported values: {supported_values}."
        )

    return tuple(normalized)


def _ensure_units_loaded() -> None:
    global _units, _units_by_id, _unit_types_by_id, _unit_classes_by_id

    if _units is not None:
        return

    with open('data/units.json') as units_file:
        raw_units = json.load(units_file)

    _units = parse_units(raw_units)
    _units_by_id = {unit.number: unit for unit in _units}
    _unit_types_by_id = {
        unit.number: _normalize_unit_types(unit.type_)
        for unit in _units
    }
    _unit_classes_by_id = {
        unit.number: _normalize_unit_classes(unit.class_)
        for unit in _units
    }


def _normalize_unit_types(raw_type: Union[str, Sequence[str]]) -> FrozenSet[str]:
    values = raw_type if isinstance(raw_type, (list, tuple, set, frozenset)) else [raw_type]
    normalized = {
        value.strip().upper()
        for value in values
        if isinstance(value, str) and value.strip().upper() in SUPPORTED_TYPES
    }
    return frozenset(normalized)


def _normalize_unit_classes(raw_class: Union[str, Sequence[str]]) -> FrozenSet[str]:
    normalized = [
        _supported_classes_by_key.get(_filter_value_key(value), value)
        for value in _extract_unit_class_values(raw_class)
    ]
    return frozenset(normalized)


def _filter_portrait_indices(portrait_ids: np.ndarray,
                             allowed_types: Sequence[str],
                             allowed_classes: Sequence[str]) -> np.ndarray:
    _ensure_units_loaded()

    allowed_set = frozenset(allowed_types)
    allowed_classes_set = frozenset(allowed_classes)
    indices = [
        index
        for index, portrait_id in enumerate(portrait_ids.tolist())
        if (
            (not allowed_set or _unit_types_by_id.get(int(portrait_id), frozenset()) & allowed_set)
            and (
                not allowed_classes_set or
                _unit_classes_by_id.get(int(portrait_id), frozenset()) & allowed_classes_set
            )
        )
    ]
    return np.asarray(indices, dtype='int32')


def _build_no_matching_candidates_message(
        allowed_types: Sequence[str],
        allowed_classes: Sequence[str]) -> str:
    active_filters: List[str] = []
    if allowed_types:
        active_filters.append(f"types: {', '.join(allowed_types)}")
    if allowed_classes:
        active_filters.append(f"classes: {', '.join(allowed_classes)}")

    filter_label = '; '.join(active_filters) if active_filters else 'no active filters'
    return (
        "No portrait candidates remain after applying the active filters "
        f"({filter_label}). Expand or clear the filter and retry."
    )


def _load_im(path: Path, size: Tuple[int, int]) -> Union[np.ndarray, None]:
    im = cv2.imread(str(path))
    if im is None or im.size == 0:
        return None
    im = trim_character_crop(im)
    return cv2.resize(im, size[::-1])


def _decorate_diagnostics(diagnostics: List[Dict[str, Any]],
                          valid_paths: List[Path],
                          selected_ids: List[int]) -> None:
    for index, diagnostic in enumerate(diagnostics):
        diagnostic['selectedNumber'] = selected_ids[index]

        for rank, candidate in enumerate(diagnostic.get('topCandidates', []), start=1):
            portrait_index = candidate.pop('portrait_index', None)
            if portrait_index is None:
                continue

            portrait_path = valid_paths[portrait_index]
            candidate['rank'] = rank
            candidate['number'] = int(portrait_path.stem)
            candidate['portraitPath'] = str(portrait_path)


def _top_similarities(characters: np.ndarray,
                      portraits: np.ndarray,
                      method: str = 'mse',
                      return_diagnostics: bool = False,
                      shortlist_size: int = 25,
                      rerank_size: int = 8,
                      diagnostics_count: int = 5,
                      portrait_indices: Optional[np.ndarray] = None) -> Tuple[List[int], Union[List[Dict[str, Any]], None]]:

    print('Computing distances...')
    if method == 'mse':
        return _two_stage_mse(
            characters,
            portraits,
            return_diagnostics=return_diagnostics,
            shortlist_size=shortlist_size,
            rerank_size=rerank_size,
            diagnostics_count=diagnostics_count,
        )

    if method == 'ssim':
        from skimage.metrics import structural_similarity as ssim

        distances = []
        pool = mp.Pool(mp.cpu_count())
        for c in tqdm.tqdm(characters):
            dist_fn = functools.partial(ssim, im2=c, multichannel=True)
            cur_dists = list(
                tqdm.tqdm(pool.imap(dist_fn, portraits), total=len(portraits)))
            distances.append(cur_dists)
        pool.close()

        distances = np.array(distances)
        best_matches = np.argmax(distances, -1).tolist()

        diagnostics = None
        if return_diagnostics:
            diagnostics = []
            for i, best_index in enumerate(best_matches):
                diagnostics.append({
                    'selectedIndex': int(best_index),
                    'topCandidates': [{
                        'portrait_index': int(best_index),
                        'final_similarity': float(distances[i, best_index]),
                    }],
                })

        return best_matches, diagnostics

    if method == 'feature_vectors':
        import torch
        import torch.nn.functional as F
        from optcbx.nn.features import (feature_extractor, get_feature_vector,
                                        load_portrait_features)

        m = feature_extractor()
        portraits_features = load_portrait_features()
        if portrait_indices is not None:
            portraits_features = portraits_features[:, torch.as_tensor(portrait_indices, dtype=torch.long), :]

        units_features = get_feature_vector(m, characters, 3).cpu()
        units_features = units_features.view(len(characters), 1, -1)

        similarities = F.cosine_similarity(units_features,
                                           portraits_features,
                                           dim=-1)
        best_matches = similarities.argmax(-1).tolist()

        diagnostics = None
        if return_diagnostics:
            diagnostics = []
            for i, best_index in enumerate(best_matches):
                diagnostics.append({
                    'selectedIndex': int(best_index),
                    'topCandidates': [{
                        'portrait_index': int(best_index),
                        'final_similarity': float(similarities[i, best_index]),
                    }],
                })

        return best_matches, diagnostics

    raise ValueError(f"Method {method} not supported")


def _two_stage_mse(characters: np.ndarray,
                   portraits: np.ndarray,
                   return_diagnostics: bool = False,
                   shortlist_size: int = 25,
                   rerank_size: int = 8,
                   diagnostics_count: int = 5) -> Tuple[List[int], Union[List[Dict[str, Any]], None]]:
    characters_f = characters.astype('float32')
    portraits_f = portraits.astype('float32')

    cd = characters_f.reshape(len(characters_f), 1, -1)
    pd = portraits_f.reshape(1, len(portraits_f), -1)
    full_mse = np.mean(np.square(cd - pd), -1)

    shortlist_size = max(1, min(int(shortlist_size), len(portraits_f)))
    rerank_size = max(1, min(int(rerank_size), shortlist_size))
    diagnostics_count = max(1, min(int(diagnostics_count), rerank_size))

    if shortlist_size == len(portraits_f):
        shortlist_indices = np.argsort(full_mse, axis=1)
    else:
        shortlist_indices = np.argpartition(full_mse, shortlist_size - 1, axis=1)[:, :shortlist_size]

    weight_map = _get_weight_map(tuple(characters.shape[1:3]))
    inner_y, inner_x = _get_inner_slice(tuple(characters.shape[1:3]))

    best_matches: List[int] = []
    diagnostics: Union[List[Dict[str, Any]], None] = [] if return_diagnostics else None

    for i in range(len(characters_f)):
        candidate_indices = shortlist_indices[i]
        candidate_indices = candidate_indices[np.argsort(full_mse[i, candidate_indices])]
        candidate_indices = candidate_indices[:rerank_size]

        candidate_images = portraits_f[candidate_indices]
        character_image = characters_f[i]

        weighted_mse = _weighted_mse(character_image, candidate_images, weight_map)
        inner_mse = _inner_mse(character_image, candidate_images, inner_y, inner_x)
        stage1_mse = full_mse[i, candidate_indices]
        final_mse = (stage1_mse * 0.20) + (weighted_mse * 0.50) + (inner_mse * 0.30)

        reranked_order = np.argsort(final_mse)
        candidate_indices = candidate_indices[reranked_order]
        stage1_mse = stage1_mse[reranked_order]
        weighted_mse = weighted_mse[reranked_order]
        inner_mse = inner_mse[reranked_order]
        final_mse = final_mse[reranked_order]

        selected_index = int(candidate_indices[0])
        best_matches.append(selected_index)

        if diagnostics is not None:
            diagnostics.append({
                'selectedIndex': selected_index,
                'topCandidates': [{
                    'portrait_index': int(candidate_indices[j]),
                    'stage1Mse': float(stage1_mse[j]),
                    'weightedMse': float(weighted_mse[j]),
                    'innerMse': float(inner_mse[j]),
                    'finalMse': float(final_mse[j]),
                } for j in range(min(diagnostics_count, len(candidate_indices)))],
            })

    return best_matches, diagnostics


def _weighted_mse(character_image: np.ndarray,
                  candidate_images: np.ndarray,
                  weight_map: np.ndarray) -> np.ndarray:
    diff = np.square(candidate_images - character_image[None, ...])
    denom = float(np.sum(weight_map) * diff.shape[-1])
    return np.sum(diff * weight_map[None, ...], axis=(1, 2, 3)) / denom


def _inner_mse(character_image: np.ndarray,
               candidate_images: np.ndarray,
               inner_y: slice,
               inner_x: slice) -> np.ndarray:
    diff = np.square(candidate_images[:, inner_y, inner_x, :] - character_image[inner_y, inner_x, :][None, ...])
    return np.mean(diff, axis=(1, 2, 3))


def _get_weight_map(image_size: Tuple[int, int]) -> np.ndarray:
    if image_size in _weight_maps:
        return _weight_maps[image_size]

    height, width = image_size
    weight = np.ones((height, width, 1), dtype='float32')

    border = max(1, int(round(min(height, width) * 0.04)))
    top_left_y = max(1, int(round(height * 0.26)))
    top_left_x = max(1, int(round(width * 0.24)))
    top_right_y = max(1, int(round(height * 0.22)))
    top_right_x = min(width, int(round(width * 0.64)))
    support_y1 = min(height, int(round(height * 0.28)))
    support_y2 = min(height, int(round(height * 0.78)))
    support_x1 = min(width, int(round(width * 0.74)))
    bottom_y = min(height, int(round(height * 0.78)))

    weight[:border, :, :] *= 0.35
    weight[-border:, :, :] *= 0.25
    weight[:, :border, :] *= 0.35
    weight[:, -border:, :] *= 0.35
    weight[:top_left_y, :top_left_x, :] *= 0.08
    weight[:top_right_y, top_right_x:, :] *= 0.15
    weight[support_y1:support_y2, support_x1:, :] *= 0.20
    weight[bottom_y:, :, :] *= 0.18

    center_y1 = min(height, int(round(height * 0.16)))
    center_y2 = min(height, int(round(height * 0.76)))
    center_x1 = min(width, int(round(width * 0.12)))
    center_x2 = min(width, int(round(width * 0.88)))
    weight[center_y1:center_y2, center_x1:center_x2, :] *= 1.45

    _weight_maps[image_size] = weight
    return weight


def _get_inner_slice(image_size: Tuple[int, int]) -> Tuple[slice, slice]:
    if image_size in _inner_slices:
        return _inner_slices[image_size]

    height, width = image_size
    inner_y = slice(int(round(height * 0.18)), max(int(round(height * 0.19)), int(round(height * 0.78))))
    inner_x = slice(int(round(width * 0.12)), max(int(round(width * 0.13)), int(round(width * 0.88))))
    _inner_slices[image_size] = (inner_y, inner_x)
    return _inner_slices[image_size]
