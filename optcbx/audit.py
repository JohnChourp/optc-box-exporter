import base64
import html
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import click
import numpy as np
from PIL import Image

import optcbx
from optcbx.units import Character, parse_units

CASE_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
INPUT_DIR_NAME = 'input'
OUTPUT_DIR_NAME = 'output'
META_DIR_NAME = 'meta'
CORRECTED_FILE_NAME = 'corrected.json'
NOTES_FILE_NAME = 'notes.txt'
FAVORITES_GLOB_PATTERNS = ('optcbx-favorites-*.json', 'favorites*.json')


@click.command('audit-case')
@click.argument('case_folder', type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('--image-size', type=int, default=64, show_default=True)
@click.option('--write-artifacts/--no-write-artifacts', default=True, show_default=True)
@click.option('--fail-on-mismatch/--no-fail-on-mismatch', default=False, show_default=True)
def main(case_folder: Path,
         image_size: int,
         write_artifacts: bool,
         fail_on_mismatch: bool) -> None:
    """Run OPTCbx against a strict case folder with input/output/meta layout."""
    report = run_audit_case(case_folder, image_size=image_size, write_artifacts=write_artifacts)
    click.echo(json.dumps(report, indent=2))

    if fail_on_mismatch and report['summary']['status'] != 'exact_match':
        raise SystemExit(1)


def run_audit_case(case_folder: Path,
                   image_size: int = 64,
                   write_artifacts: bool = True) -> Dict[str, Any]:
    case_context = load_case_context(case_folder)
    screenshot = _load_screenshot(case_context['inputImagePath'])
    units_by_id = _load_units_by_id()

    current_match_result = optcbx.find_characters_from_screenshot(
        screenshot,
        image_size=image_size,
        return_thumbnails=True,
        return_diagnostics=True,
        approach='gradient_based',
    )
    current_characters, current_thumbnails, current_diagnostics = current_match_result
    current_character_entries = [_character_to_entry(character) for character in current_characters]

    provided_crops = _load_provided_output_crops(case_context['outputImagePaths'], image_size=image_size)
    provided_ids, provided_diagnostics = optcbx.find_characters_ids(
        provided_crops,
        return_diagnostics=True,
    )
    provided_character_entries = [
        _character_entry_from_id(character_id, units_by_id)
        for character_id in provided_ids
    ]

    current_track = build_track_report(
        expected_entries=case_context['expectedCharacters'],
        actual_entries=current_character_entries,
        diagnostics=current_diagnostics,
        units_by_id=units_by_id,
    )
    provided_track = build_track_report(
        expected_entries=case_context['expectedCharacters'],
        actual_entries=provided_character_entries,
        diagnostics=provided_diagnostics,
        units_by_id=units_by_id,
    )

    favorites_checks = build_favorites_consistency_checks(
        expected_entries=case_context['expectedCharacters'],
        favorites_entries=case_context['favoritesCharacters'],
        favorites_path=case_context['favoritesPath'],
    )

    report = build_audit_report(
        case_context=case_context,
        current_track=current_track,
        provided_track=provided_track,
        favorites_checks=favorites_checks,
    )

    current_export_payload = build_export_payload(
        case_id=case_context['caseId'],
        image_size=image_size,
        source='current_rerun',
        characters=current_character_entries,
    )
    provided_export_payload = build_export_payload(
        case_id=case_context['caseId'],
        image_size=image_size,
        source='provided_output_baseline',
        characters=provided_character_entries,
    )

    if write_artifacts:
        write_case_artifacts(
            case_folder=case_context['caseFolder'],
            current_export_payload=current_export_payload,
            provided_export_payload=provided_export_payload,
            report=report,
            current_thumbnails=current_thumbnails,
            provided_thumbnails=provided_crops,
        )

    return report


def load_case_context(case_folder: Path) -> Dict[str, Any]:
    input_dir = _require_case_subdir(case_folder, INPUT_DIR_NAME)
    output_dir = _require_case_subdir(case_folder, OUTPUT_DIR_NAME)
    meta_dir = _require_case_subdir(case_folder, META_DIR_NAME)

    input_image_path = discover_single_input_image(input_dir)
    output_image_paths = discover_output_images(output_dir)
    corrected_path = meta_dir / CORRECTED_FILE_NAME

    if not corrected_path.exists():
        raise FileNotFoundError(f"Missing {META_DIR_NAME}/{CORRECTED_FILE_NAME} in {case_folder}")

    raw_payload = json.loads(corrected_path.read_text())
    if not isinstance(raw_payload, dict):
        raise ValueError(f"{META_DIR_NAME}/{CORRECTED_FILE_NAME} must contain a JSON object.")

    expected_characters = _parse_expected_characters(raw_payload, file_label=CORRECTED_FILE_NAME)

    notes_path = meta_dir / NOTES_FILE_NAME
    notes = raw_payload.get('notes')
    if notes_path.exists() and (not isinstance(notes, str) or not notes.strip()):
        notes = notes_path.read_text().strip()

    case_id = raw_payload.get('caseId')
    if not isinstance(case_id, str) or not case_id.strip():
        case_id = case_folder.name

    favorites_path = discover_latest_favorites_json(meta_dir)
    favorites_characters = load_favorites_characters(favorites_path) if favorites_path else []

    return {
        'caseFolder': case_folder,
        'caseId': case_id.strip(),
        'inputImagePath': input_image_path,
        'outputImagePaths': output_image_paths,
        'correctedPath': corrected_path,
        'favoritesPath': favorites_path,
        'notes': notes.strip() if isinstance(notes, str) and notes.strip() else '',
        'expectedCharacters': expected_characters,
        'favoritesCharacters': favorites_characters,
    }


def _require_case_subdir(case_folder: Path, folder_name: str) -> Path:
    subdir = case_folder / folder_name
    if not subdir.is_dir():
        raise FileNotFoundError(f"Missing required directory: {subdir}")
    return subdir


def discover_single_input_image(input_dir: Path) -> Path:
    image_paths = sorted([
        path for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in CASE_IMAGE_SUFFIXES
    ])

    if len(image_paths) != 1:
        raise ValueError(
            f"Expected exactly one input screenshot image in {input_dir}, found {len(image_paths)}."
        )

    return image_paths[0]


def discover_output_images(output_dir: Path) -> List[Path]:
    image_paths = [
        path for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in CASE_IMAGE_SUFFIXES
    ]

    if not image_paths:
        raise ValueError(f"Expected at least one output character image in {output_dir}, found 0.")

    return sorted(image_paths, key=_natural_sort_key)


def _natural_sort_key(path: Path) -> List[Union[int, str]]:
    parts = re.split(r'(\d+)', path.name.casefold())
    key: List[Union[int, str]] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return key


def _parse_expected_characters(raw_payload: Dict[str, Any], file_label: str) -> List[Dict[str, Any]]:
    raw_characters = raw_payload.get('characters')
    if not isinstance(raw_characters, list) or len(raw_characters) == 0:
        raise ValueError(f"{META_DIR_NAME}/{file_label} must contain a non-empty characters array.")

    characters = []
    for index, entry in enumerate(raw_characters, start=1):
        if not isinstance(entry, dict):
            raise ValueError(
                f"Character entry {index} in {META_DIR_NAME}/{file_label} must be an object."
            )

        number = entry.get('number')
        name = entry.get('name')

        if not isinstance(number, int) or number <= 0:
            raise ValueError(
                f"Character entry {index} in {META_DIR_NAME}/{file_label} "
                "is missing a valid positive integer number."
            )
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Character entry {index} in {META_DIR_NAME}/{file_label} is missing a valid name."
            )

        characters.append({
            'number': number,
            'name': name.strip(),
            'position': index - 1,
        })

    return characters


def discover_latest_favorites_json(meta_dir: Path) -> Optional[Path]:
    candidates: Dict[Path, None] = {}
    for pattern in FAVORITES_GLOB_PATTERNS:
        for path in meta_dir.glob(pattern):
            if path.is_file() and path.name != CORRECTED_FILE_NAME:
                candidates[path] = None

    if not candidates:
        return None

    return max(candidates.keys(), key=lambda path: (path.stat().st_mtime, path.name))


def load_favorites_characters(favorites_path: Path) -> List[Dict[str, Any]]:
    raw_payload = json.loads(favorites_path.read_text())
    if not isinstance(raw_payload, dict):
        raise ValueError(f"{favorites_path} must contain a JSON object.")

    raw_characters = raw_payload.get('characters')
    if not isinstance(raw_characters, list):
        raise ValueError(f"{favorites_path} must contain a characters array.")

    characters = []
    for index, entry in enumerate(raw_characters, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Character entry {index} in {favorites_path.name} must be an object.")

        number = entry.get('number')
        if not isinstance(number, int) or number <= 0:
            raise ValueError(
                f"Character entry {index} in {favorites_path.name} has invalid number: {number}."
            )

        name = entry.get('name')
        characters.append({
            'number': number,
            'name': name.strip() if isinstance(name, str) else '',
        })

    return characters


def build_export_payload(case_id: str,
                        image_size: int,
                        source: str,
                        characters: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        'caseId': case_id,
        'imageSize': image_size,
        'source': source,
        'characters': [dict(character) for character in characters],
    }


def _character_to_entry(character: Character) -> Dict[str, Any]:
    return {
        'name': character.name,
        'type_': character.type_,
        'class_': character.class_,
        'stars': character.stars,
        'number': character.number,
    }


def _character_entry_from_id(character_id: int,
                             units_by_id: Dict[int, Character]) -> Dict[str, Any]:
    character = units_by_id.get(character_id)
    if character is None:
        return {
            'name': '',
            'type_': '',
            'class_': [],
            'stars': '',
            'number': character_id,
        }

    return _character_to_entry(character)


def build_track_report(expected_entries: List[Dict[str, Any]],
                       actual_entries: List[Dict[str, Any]],
                       diagnostics: List[Dict[str, Any]],
                       units_by_id: Dict[int, Character]) -> Dict[str, Any]:
    comparisons = []
    mismatch_categories = set()
    matched_count = 0

    total = max(len(expected_entries), len(actual_entries))
    for index in range(total):
        expected_entry = expected_entries[index] if index < len(expected_entries) else None
        actual_entry = actual_entries[index] if index < len(actual_entries) else None
        diagnostic_entry = diagnostics[index] if index < len(diagnostics) else None

        comparison = build_comparison_entry(index, expected_entry, actual_entry, diagnostic_entry, units_by_id)
        comparisons.append(comparison)

        if comparison['status'] == 'matched':
            matched_count += 1
            continue

        mismatch_categories.add(comparison['mismatchCategory'])

    status = 'exact_match' if not mismatch_categories else 'mismatch'
    return {
        'summary': {
            'status': status,
            'expectedCount': len(expected_entries),
            'actualCount': len(actual_entries),
            'matchedCount': matched_count,
            'mismatchCount': total - matched_count,
            'mismatchCategories': sorted(mismatch_categories),
        },
        'comparisons': comparisons,
    }


def build_audit_report(case_context: Dict[str, Any],
                       current_track: Dict[str, Any],
                       provided_track: Dict[str, Any],
                       favorites_checks: Dict[str, Any]) -> Dict[str, Any]:
    current_summary = current_track['summary']
    provided_summary = provided_track['summary']

    return {
        'caseId': case_context['caseId'],
        'inputImagePath': str(case_context['inputImagePath']),
        'providedOutputImagePaths': [str(path) for path in case_context['outputImagePaths']],
        'correctedPath': str(case_context['correctedPath']),
        'favoritesPath': str(case_context['favoritesPath']) if case_context['favoritesPath'] else None,
        'notes': case_context['notes'],
        'summary': {
            'status': current_summary['status'],
            'currentStatus': current_summary['status'],
            'providedStatus': provided_summary['status'],
            'currentMismatchCategories': current_summary['mismatchCategories'],
            'providedMismatchCategories': provided_summary['mismatchCategories'],
        },
        'current': current_track,
        'provided': provided_track,
        'favoritesChecks': favorites_checks,
    }


def build_favorites_consistency_checks(expected_entries: List[Dict[str, Any]],
                                       favorites_entries: List[Dict[str, Any]],
                                       favorites_path: Optional[Path]) -> Dict[str, Any]:
    if favorites_path is None:
        return {
            'status': 'not_provided',
            'path': None,
            'favoritesUniqueCount': 0,
            'expectedUniqueCount': len({entry['number'] for entry in expected_entries}),
            'missingFromFavorites': [],
            'extraInFavorites': [],
        }

    expected_set = {entry['number'] for entry in expected_entries}
    favorites_set = {entry['number'] for entry in favorites_entries}

    missing_from_favorites = sorted(expected_set - favorites_set)
    extra_in_favorites = sorted(favorites_set - expected_set)

    status = 'consistent' if not missing_from_favorites and not extra_in_favorites else 'mismatch'

    return {
        'status': status,
        'path': str(favorites_path),
        'favoritesUniqueCount': len(favorites_set),
        'expectedUniqueCount': len(expected_set),
        'missingFromFavorites': missing_from_favorites,
        'extraInFavorites': extra_in_favorites,
    }


def build_comparison_entry(index: int,
                           expected_entry: Dict[str, Any],
                           actual_entry: Dict[str, Any],
                           diagnostic_entry: Dict[str, Any],
                           units_by_id: Dict[int, Character]) -> Dict[str, Any]:
    expected_number = expected_entry['number'] if expected_entry else None
    actual_number = actual_entry['number'] if actual_entry else None

    if expected_entry and actual_entry and expected_number == actual_number:
        status = 'matched'
        mismatch_category = None
    elif expected_entry is None or actual_entry is None:
        status = 'mismatch'
        mismatch_category = 'detection_count_mismatch'
    elif expected_number not in units_by_id:
        status = 'mismatch'
        mismatch_category = 'expected_id_missing_from_local_units'
    elif not (Path('data/Portraits') / f"{expected_number}.png").exists():
        status = 'mismatch'
        mismatch_category = 'missing_portrait_asset'
    else:
        status = 'mismatch'
        mismatch_category = 'wrong_match_dataset_present'

    top_candidates = []
    if diagnostic_entry:
        for candidate in diagnostic_entry.get('topCandidates', []):
            candidate_number = candidate.get('number')
            candidate_name = units_by_id[candidate_number].name if candidate_number in units_by_id else ''
            top_candidates.append({
                **candidate,
                'name': candidate_name,
            })

    return {
        'position': index,
        'status': status,
        'mismatchCategory': mismatch_category,
        'expected': expected_entry,
        'actual': actual_entry,
        'topCandidates': top_candidates,
    }


def write_case_artifacts(case_folder: Path,
                         current_export_payload: Dict[str, Any],
                         provided_export_payload: Dict[str, Any],
                         report: Dict[str, Any],
                         current_thumbnails: np.ndarray,
                         provided_thumbnails: np.ndarray) -> None:
    current_export_path = case_folder / 'actual-export.json'
    provided_export_path = case_folder / 'provided-export.json'
    report_path = case_folder / 'audit-report.json'
    current_html_path = case_folder / 'actual-grid.html'
    provided_html_path = case_folder / 'provided-grid.html'

    current_export_path.write_text(json.dumps(current_export_payload, indent=2) + '\n')
    provided_export_path.write_text(json.dumps(provided_export_payload, indent=2) + '\n')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    current_html_path.write_text(render_grid_html(
        case_id=report['caseId'],
        track_label='Current rerun from input screenshot',
        track_data=report['current'],
        thumbnails=current_thumbnails,
    ))
    provided_html_path.write_text(render_grid_html(
        case_id=report['caseId'],
        track_label='Provided output images baseline',
        track_data=report['provided'],
        thumbnails=provided_thumbnails,
    ))


def render_grid_html(case_id: str,
                     track_label: str,
                     track_data: Dict[str, Any],
                     thumbnails: np.ndarray) -> str:
    cards = []
    comparisons = track_data['comparisons']

    for index, comparison in enumerate(comparisons):
        actual = comparison.get('actual')
        expected = comparison.get('expected')
        thumbnail_uri = image_to_data_uri(thumbnails[index]) if index < len(thumbnails) else ''
        card_class = 'result-card result-card--ok' if comparison['status'] == 'matched' else 'result-card result-card--warn'
        thumbnail_html = (
            f'<img src="{thumbnail_uri}" alt="crop {index + 1}">'
            if thumbnail_uri else
            '<div class="result-card__thumb-empty">No crop</div>'
        )

        title = actual['name'] if actual else 'No detected character'
        number = f"#{actual['number']}" if actual else 'No id'
        expected_html = ''
        if comparison['status'] != 'matched' and expected:
            expected_html = (
                f'<div class="result-card__expected">'
                f'Expected: #{expected["number"]} - {html.escape(expected["name"])}'
                f'</div>'
            )

        cards.append(
            f'<article class="{card_class}">'
            f'<div class="result-card__thumb">{thumbnail_html}</div>'
            f'<div class="result-card__meta">'
            f'<div class="result-card__id">{number}</div>'
            f'<div class="result-card__name">{html.escape(title)}</div>'
            f'{expected_html}'
            f'</div>'
            f'</article>'
        )

    mismatch_categories = ', '.join(track_data['summary']['mismatchCategories']) or 'none'
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <title>OPTCbx Audit - {html.escape(case_id)} - {html.escape(track_label)}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      font-family: Arial, sans-serif;
      background: #111827;
      color: #f3f4f6;
    }}
    .summary {{
      margin-bottom: 24px;
      padding: 18px 20px;
      border-radius: 18px;
      background: rgba(17, 24, 39, 0.75);
      border: 1px solid rgba(148, 163, 184, 0.2);
    }}
    .summary strong {{
      display: block;
      font-size: 28px;
      margin-bottom: 6px;
    }}
    .summary span {{
      display: block;
      color: #cbd5e1;
      margin-top: 4px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
    }}
    .result-card {{
      display: grid;
      grid-template-columns: 96px 1fr;
      gap: 14px;
      padding: 14px;
      border-radius: 18px;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(148, 163, 184, 0.18);
      min-height: 132px;
    }}
    .result-card--ok {{
      box-shadow: inset 0 0 0 1px rgba(74, 222, 128, 0.15);
    }}
    .result-card--warn {{
      box-shadow: inset 0 0 0 1px rgba(251, 191, 36, 0.2);
    }}
    .result-card__thumb {{
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 14px;
      background: #020617;
      overflow: hidden;
    }}
    .result-card__thumb img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .result-card__thumb-empty {{
      color: #94a3b8;
      font-size: 12px;
      text-align: center;
      padding: 12px;
    }}
    .result-card__id {{
      font-weight: 700;
      color: #facc15;
      margin-bottom: 8px;
    }}
    .result-card__name {{
      font-size: 17px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .result-card__expected {{
      margin-top: 10px;
      color: #cbd5e1;
      font-size: 13px;
      line-height: 1.4;
    }}
    @media (max-width: 1100px) {{
      .grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 700px) {{
      .grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <section class=\"summary\">
    <strong>{html.escape(case_id)}</strong>
    <span>Track: {html.escape(track_label)}</span>
    <span>Status: {track_data['summary']['status']}</span>
    <span>Expected: {track_data['summary']['expectedCount']} | Actual: {track_data['summary']['actualCount']} | Matched: {track_data['summary']['matchedCount']}</span>
    <span>Mismatch categories: {mismatch_categories}</span>
  </section>
  <section class=\"grid\">
    {''.join(cards)}
  </section>
</body>
</html>
"""


def image_to_data_uri(image: np.ndarray) -> str:
    image_rgb = Image.fromarray(np.flip(image, -1))
    buffer = io.BytesIO()
    image_rgb.save(buffer, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()


def _load_screenshot(image_path: Path) -> np.ndarray:
    with Image.open(image_path) as image:
        rgb = image.convert('RGB')
    return np.flip(np.array(rgb), -1).copy()


def _load_provided_output_crops(output_image_paths: List[Path], image_size: int) -> np.ndarray:
    if image_size <= 0:
        raise ValueError('image_size must be a positive integer.')

    resample = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
    thumbnails = []

    for image_path in output_image_paths:
        with Image.open(image_path) as image:
            rgb = image.convert('RGB')
            if rgb.size != (image_size, image_size):
                rgb = rgb.resize((image_size, image_size), resample=resample)
        thumbnails.append(np.flip(np.array(rgb), -1).copy())

    return np.stack(thumbnails, axis=0)


def _load_units_by_id() -> Dict[int, Character]:
    with open('data/units.json') as units_file:
        units = json.load(units_file)
    parsed = parse_units(units)
    return {character.number: character for character in parsed}
