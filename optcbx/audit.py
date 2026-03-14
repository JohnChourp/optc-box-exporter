import base64
import html
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import click
import numpy as np
from PIL import Image

import optcbx
from optcbx.units import Character, parse_units

CASE_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
EXPECTED_FILE_NAME = 'expected.json'
NOTES_FILE_NAME = 'notes.txt'


@click.command('audit-case')
@click.argument('case_folder', type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('--image-size', type=int, default=64, show_default=True)
@click.option('--write-artifacts/--no-write-artifacts', default=True, show_default=True)
@click.option('--fail-on-mismatch/--no-fail-on-mismatch', default=False, show_default=True)
def main(case_folder: Path,
         image_size: int,
         write_artifacts: bool,
         fail_on_mismatch: bool) -> None:
    """Run OPTCbx against a case folder with source screenshot + expected.json."""
    report = run_audit_case(case_folder, image_size=image_size, write_artifacts=write_artifacts)
    click.echo(json.dumps(report, indent=2))

    if fail_on_mismatch and report['summary']['status'] != 'exact_match':
        raise SystemExit(1)


def run_audit_case(case_folder: Path,
                   image_size: int = 64,
                   write_artifacts: bool = True) -> Dict[str, Any]:
    case_context = load_case_context(case_folder)
    screenshot = _load_screenshot(case_context['imagePath'])
    units_by_id = _load_units_by_id()

    match_result = optcbx.find_characters_from_screenshot(
        screenshot,
        image_size=image_size,
        return_thumbnails=True,
        return_diagnostics=True,
        approach='gradient_based',
    )
    actual_characters, thumbnails, diagnostics = match_result
    export_payload = build_actual_export_payload(case_context['caseId'], image_size, actual_characters)
    report = build_audit_report(case_context, actual_characters, diagnostics, units_by_id)

    if write_artifacts:
        write_case_artifacts(case_context['caseFolder'], export_payload, report, thumbnails)

    return report


def load_case_context(case_folder: Path) -> Dict[str, Any]:
    image_path = discover_case_image(case_folder)
    expected_path = case_folder / EXPECTED_FILE_NAME

    if not expected_path.exists():
        raise FileNotFoundError(f"Missing {EXPECTED_FILE_NAME} in {case_folder}")

    raw_payload = json.loads(expected_path.read_text())
    if not isinstance(raw_payload, dict):
        raise ValueError(f"{EXPECTED_FILE_NAME} must contain a JSON object.")

    raw_characters = raw_payload.get('characters')
    if not isinstance(raw_characters, list) or len(raw_characters) == 0:
        raise ValueError(f"{EXPECTED_FILE_NAME} must contain a non-empty characters array.")

    characters = []
    for index, entry in enumerate(raw_characters, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Character entry {index} in {EXPECTED_FILE_NAME} must be an object.")

        number = entry.get('number')
        name = entry.get('name')

        if not isinstance(number, int) or number <= 0:
            raise ValueError(f"Character entry {index} is missing a valid positive integer number.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Character entry {index} is missing a valid name.")

        characters.append({
            'number': number,
            'name': name.strip(),
            'position': index - 1,
        })

    notes_path = case_folder / NOTES_FILE_NAME
    notes = raw_payload.get('notes')
    if notes_path.exists() and (not isinstance(notes, str) or not notes.strip()):
        notes = notes_path.read_text().strip()

    case_id = raw_payload.get('caseId')
    if not isinstance(case_id, str) or not case_id.strip():
        case_id = case_folder.name

    return {
        'caseFolder': case_folder,
        'caseId': case_id.strip(),
        'imagePath': image_path,
        'notes': notes.strip() if isinstance(notes, str) and notes.strip() else '',
        'expectedCharacters': characters,
    }


def discover_case_image(case_folder: Path) -> Path:
    image_paths = sorted([
        path for path in case_folder.iterdir()
        if path.is_file() and path.suffix.lower() in CASE_IMAGE_SUFFIXES
    ])

    if len(image_paths) != 1:
        raise ValueError(
            f"Expected exactly one screenshot image in {case_folder}, found {len(image_paths)}."
        )

    return image_paths[0]


def build_actual_export_payload(case_id: str,
                                image_size: int,
                                characters: List[Character]) -> Dict[str, Any]:
    return {
        'caseId': case_id,
        'imageSize': image_size,
        'characters': [dict(character._asdict()) for character in characters],
    }


def build_audit_report(case_context: Dict[str, Any],
                       actual_characters: List[Character],
                       diagnostics: List[Dict[str, Any]],
                       units_by_id: Dict[int, Character]) -> Dict[str, Any]:
    expected = case_context['expectedCharacters']
    comparisons = []
    mismatch_categories = set()
    matched_count = 0

    total = max(len(expected), len(actual_characters))
    for index in range(total):
        expected_entry = expected[index] if index < len(expected) else None
        actual_entry = actual_characters[index] if index < len(actual_characters) else None
        diagnostic_entry = diagnostics[index] if index < len(diagnostics) else None

        comparison = build_comparison_entry(index, expected_entry, actual_entry, diagnostic_entry, units_by_id)
        comparisons.append(comparison)

        if comparison['status'] == 'matched':
            matched_count += 1
            continue

        mismatch_categories.add(comparison['mismatchCategory'])

    summary_status = 'exact_match' if not mismatch_categories else 'mismatch'
    return {
        'caseId': case_context['caseId'],
        'imagePath': str(case_context['imagePath']),
        'notes': case_context['notes'],
        'summary': {
            'status': summary_status,
            'expectedCount': len(expected),
            'actualCount': len(actual_characters),
            'matchedCount': matched_count,
            'mismatchCount': total - matched_count,
            'mismatchCategories': sorted(mismatch_categories),
        },
        'comparisons': comparisons,
    }


def build_comparison_entry(index: int,
                           expected_entry: Dict[str, Any],
                           actual_entry: Character,
                           diagnostic_entry: Dict[str, Any],
                           units_by_id: Dict[int, Character]) -> Dict[str, Any]:
    if expected_entry and actual_entry and expected_entry['number'] == actual_entry.number:
        status = 'matched'
        mismatch_category = None
    elif expected_entry is None or actual_entry is None:
        status = 'mismatch'
        mismatch_category = 'detection_count_mismatch'
    elif expected_entry['number'] not in units_by_id:
        status = 'mismatch'
        mismatch_category = 'expected_id_missing_from_local_units'
    elif not (Path('data/Portraits') / f"{expected_entry['number']}.png").exists():
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
        'actual': dict(actual_entry._asdict()) if actual_entry else None,
        'topCandidates': top_candidates,
    }


def write_case_artifacts(case_folder: Path,
                         export_payload: Dict[str, Any],
                         report: Dict[str, Any],
                         thumbnails: np.ndarray) -> None:
    export_path = case_folder / 'actual-export.json'
    report_path = case_folder / 'audit-report.json'
    html_path = case_folder / 'actual-grid.html'

    export_path.write_text(json.dumps(export_payload, indent=2) + '\n')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    html_path.write_text(render_actual_grid_html(report, thumbnails))


def render_actual_grid_html(report: Dict[str, Any], thumbnails: np.ndarray) -> str:
    cards = []
    comparisons = report['comparisons']

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

    categories = ', '.join(report['summary']['mismatchCategories']) or 'none'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OPTCbx Audit - {html.escape(report['caseId'])}</title>
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
  <section class="summary">
    <strong>{html.escape(report['caseId'])}</strong>
    <span>Status: {report['summary']['status']}</span>
    <span>Expected: {report['summary']['expectedCount']} | Actual: {report['summary']['actualCount']} | Matched: {report['summary']['matchedCount']}</span>
    <span>Mismatch categories: {categories}</span>
  </section>
  <section class="grid">
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


def _load_units_by_id() -> Dict[int, Character]:
    with open('data/units.json') as units_file:
        units = json.load(units_file)
    parsed = parse_units(units)
    return {character.number: character for character in parsed}
