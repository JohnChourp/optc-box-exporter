import json
import multiprocessing as mp
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import click
import requests
from optcbx.units import viable_unit
from tqdm.contrib.concurrent import thread_map

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / 'data'
REPORT_PATH = DATA_DIR / 'portrait-sync-report.json'
MANIFEST_SCRIPT = ROOT_DIR / 'tools' / 'build_portrait_manifest.mjs'
RAW_GITHUB_BASE = 'https://raw.githubusercontent.com/optc-db/optc-db.github.io/master'
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
GLO_CACHE_SUBPATH = (
    'public',
    'assets',
    'offline-packs',
    'thumbnails-glo',
)
DEFAULT_TIMEOUT = 30
PACK_KEY_TO_REGION = {
    'thumbnailsGlo': 'glo',
    'thumbnailsJapan': 'jap',
}


def _default_team_builder_root() -> Optional[Path]:
    candidate = ROOT_DIR.parent / 'optc-team-builder'
    if candidate.exists():
        return candidate
    return None


def _is_valid_png(path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return False

    with open(path, 'rb') as handle:
        return handle.read(8) == PNG_SIGNATURE


def _safe_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_viable_unit_ids(units_path: Path) -> List[int]:
    if not units_path.exists():
        return []

    units = json.load(open(units_path))
    return [
        index for index, unit in enumerate(units, start=1)
        if viable_unit(unit)
    ]


def _valid_portrait_ids(portraits_path: Path) -> List[int]:
    if not portraits_path.exists():
        return []

    valid_ids: List[int] = []
    for path in portraits_path.glob('*.png'):
        portrait_id = _safe_int(path.stem)
        if portrait_id is None:
            continue
        if _is_valid_png(path):
            valid_ids.append(portrait_id)
    return sorted(valid_ids)


def _invalid_portrait_ids(portraits_path: Path) -> List[int]:
    if not portraits_path.exists():
        return []

    invalid_ids: List[int] = []
    for path in portraits_path.glob('*.png'):
        portrait_id = _safe_int(path.stem)
        if portrait_id is None:
            continue
        if not _is_valid_png(path):
            invalid_ids.append(portrait_id)
    return sorted(invalid_ids)


def _normalize_viable_ids(values: Sequence[Any]) -> List[int]:
    normalized = []
    for value in values:
        integer = _safe_int(str(value))
        if integer is not None:
            normalized.append(integer)
    return normalized


def _load_sync_report(
        report_path: Path,
        viable_ids: Sequence[int]) -> Optional[Dict[str, Any]]:
    if not report_path.exists():
        return None

    try:
        report = json.load(open(report_path))
    except json.JSONDecodeError:
        return None

    if report.get('viableCount') != len(viable_ids):
        return None

    if report.get('viableIdsHash') != _viable_ids_hash(viable_ids):
        return None

    report['unresolvedIds'] = _normalize_viable_ids(report.get('unresolvedIds', []))
    report['failedIds'] = _normalize_viable_ids(report.get('failedIds', []))
    return report


def build_local_portrait_status(units_path: Path,
                                portraits_path: Path,
                                report_path: Path = REPORT_PATH) -> Dict[str, Any]:
    units_exists = units_path.exists()
    portraits_path.mkdir(parents=True, exist_ok=True)

    viable_ids = load_viable_unit_ids(units_path) if units_exists else []
    valid_ids = _valid_portrait_ids(portraits_path)
    invalid_ids = _invalid_portrait_ids(portraits_path)
    report = _load_sync_report(report_path, viable_ids)

    unresolved_ids = report.get('unresolvedIds', []) if report else []
    failed_ids = report.get('failedIds', []) if report else []
    valid_ids_set = set(valid_ids)
    unresolved_ids_set = set(unresolved_ids)

    blocking_missing_ids = [
        portrait_id for portrait_id in viable_ids
        if portrait_id not in valid_ids_set and portrait_id not in unresolved_ids_set
    ]
    unresolved_missing_ids = [
        portrait_id for portrait_id in viable_ids
        if portrait_id not in valid_ids_set and portrait_id in unresolved_ids_set
    ]

    expected_resolvable_count = len(viable_ids) - len(unresolved_ids_set)
    resolved_ready = (
        units_exists and
        len(blocking_missing_ids) == 0 and
        all(portrait_id not in invalid_ids for portrait_id in unresolved_ids_set)
    )

    summary = (
        f"Valid portraits: {len(valid_ids_set & set(viable_ids))}/{len(viable_ids)}. "
        f"Blocking missing: {len(blocking_missing_ids)}."
    )
    if unresolved_ids:
        summary += f" Known upstream unresolved: {len(unresolved_ids)}."
    if failed_ids:
        summary += f" Last sync failures: {len(failed_ids)}."

    return {
        'units_exists': units_exists,
        'viable_ids': viable_ids,
        'viable_count': len(viable_ids),
        'valid_ids': valid_ids,
        'valid_count': len(valid_ids),
        'invalid_ids': invalid_ids,
        'invalid_count': len(invalid_ids),
        'unresolved_ids': unresolved_ids,
        'unresolved_count': len(unresolved_ids),
        'failed_ids': failed_ids,
        'failed_count': len(failed_ids),
        'expected_resolvable_count': expected_resolvable_count,
        'blocking_missing_ids': blocking_missing_ids,
        'blocking_missing_count': len(blocking_missing_ids),
        'unresolved_missing_ids': unresolved_missing_ids,
        'ready': resolved_ready,
        'report_available': report is not None,
        'summary': summary,
    }


def _load_manifest() -> Dict[str, Any]:
    node_bin = shutil.which('node')
    if not node_bin:
        raise click.ClickException(
            "Node.js is required to build the OPTC portrait manifest."
        )

    process = subprocess.run(
        [node_bin, str(MANIFEST_SCRIPT)],
        cwd=str(ROOT_DIR),
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Unable to parse portrait manifest JSON: {exc}"
        ) from exc


def _download_url_for(source_url: str) -> str:
    if source_url.startswith('http://') or source_url.startswith('https://'):
        return source_url
    return f"{RAW_GITHUB_BASE}{source_url}"


def _team_builder_cache_path(team_builder_root: Optional[Path],
                             item: Dict[str, Any]) -> Optional[Path]:
    if team_builder_root is None:
        return None
    if item.get('region') != 'glo' or not item.get('relativePath'):
        return None

    return team_builder_root.joinpath(*GLO_CACHE_SUBPATH, item['relativePath'])


def _manual_override_path(team_builder_root: Optional[Path],
                          item: Dict[str, Any]) -> Optional[Path]:
    if team_builder_root is None:
        return None
    if item.get('region') != 'manual' or not item.get('manualFile'):
        return None

    return team_builder_root / 'scripts' / 'data' / 'character-images' / item['manualFile']


def _copy_cached_glo_asset(cache_path: Optional[Path], destination: Path) -> bool:
    if cache_path is None or not cache_path.exists() or not _is_valid_png(cache_path):
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache_path, destination)
    return _is_valid_png(destination)


def _load_team_builder_overrides(
        team_builder_root: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    if team_builder_root is None:
        return {}

    overrides_path = team_builder_root / 'scripts' / 'data' / 'character-image-overrides.json'
    if not overrides_path.exists():
        return {}

    raw_overrides = json.load(open(overrides_path))
    overrides: Dict[int, Dict[str, Any]] = {}
    for raw_id, entry in raw_overrides.items():
        override_id = _safe_int(str(raw_id))
        if override_id is None or not isinstance(entry, dict):
            continue
        overrides[override_id] = entry
    return overrides


def _apply_team_builder_overrides(
        manifest_items: List[Dict[str, Any]],
        team_builder_root: Optional[Path]) -> List[Dict[str, Any]]:
    overrides = _load_team_builder_overrides(team_builder_root)
    if not overrides:
        return manifest_items

    items_by_id = {item['id']: dict(item) for item in manifest_items}

    for override_id, override in overrides.items():
        item = items_by_id.get(override_id)
        if item is None:
            continue

        source = override.get('source')
        if source == 'upstream':
            region = PACK_KEY_TO_REGION.get(override.get('packKey'))
            relative_path = override.get('relativePath')
            if region and isinstance(relative_path, str):
                item['region'] = region
                item['relativePath'] = relative_path
                item['sourceUrl'] = (
                    f"/api/images/thumbnail/{region}/{relative_path}"
                )
        elif source == 'manual':
            manual_file = override.get('file')
            if isinstance(manual_file, str):
                item['region'] = 'manual'
                item['relativePath'] = manual_file
                item['manualFile'] = manual_file
                item['sourceUrl'] = None

    return [items_by_id[item['id']] for item in manifest_items]


def _download_png(destination: Path, source_url: str) -> Tuple[str, Optional[str]]:
    try:
        response = requests.get(_download_url_for(source_url),
                                timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        return 'failed', str(exc)

    if response.status_code == 404:
        return 'unresolved', f"404 for {source_url}"

    if response.status_code != 200:
        return 'failed', f"HTTP {response.status_code} for {source_url}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)

    if _is_valid_png(destination):
        return 'downloaded', None

    destination.unlink(missing_ok=True)
    return 'unresolved', f"Invalid PNG payload for {source_url}"


def _sync_single_portrait(
        payload: Tuple[Dict[str, Any], Path, Optional[Path]]) -> Dict[str, Any]:
    item, output_path, team_builder_root = payload
    portrait_id = item['id']
    destination = output_path / f"{portrait_id}.png"
    had_invalid_file = destination.exists() and not _is_valid_png(destination)

    if _is_valid_png(destination):
        return {
            'id': portrait_id,
            'action': 'skipped',
            'repaired': False,
            'message': None,
        }

    manual_path = _manual_override_path(team_builder_root, item)
    if _copy_cached_glo_asset(manual_path, destination):
        return {
            'id': portrait_id,
            'action': 'copied',
            'repaired': had_invalid_file,
            'message': None,
        }

    if not item.get('sourceUrl'):
        destination.unlink(missing_ok=True)
        return {
            'id': portrait_id,
            'action': 'unresolved',
            'repaired': had_invalid_file,
            'message': 'No upstream thumbnail source found',
        }

    cache_path = _team_builder_cache_path(team_builder_root, item)
    if _copy_cached_glo_asset(cache_path, destination):
        return {
            'id': portrait_id,
            'action': 'copied',
            'repaired': had_invalid_file,
            'message': None,
        }

    action, message = _download_png(destination, item['sourceUrl'])
    return {
        'id': portrait_id,
        'action': action,
        'repaired': had_invalid_file and action in {'copied', 'downloaded'},
        'message': message,
    }


def _write_sync_report(report_path: Path,
                       viable_ids: Sequence[int],
                       unresolved_ids: Sequence[int],
                       failed_ids: Sequence[int],
                       manifest: Dict[str, Any],
                       summary: Dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'sourceVersion': manifest.get('sourceVersion', 'unknown'),
        'viableCount': len(viable_ids),
        'viableIdsHash': _viable_ids_hash(viable_ids),
        'unresolvedIds': sorted(unresolved_ids),
        'failedIds': sorted(failed_ids),
        'summary': {
            'copied': summary['copied'],
            'downloaded': summary['downloaded'],
            'skipped': summary['skipped'],
            'repaired': summary['repaired'],
            'unresolved': summary['unresolved'],
            'failed': summary['failed'],
        },
    }
    report_path.write_text(json.dumps(report, indent=2) + '\n')


def _viable_ids_hash(viable_ids: Sequence[int]) -> str:
    payload = ','.join(map(str, viable_ids)).encode()
    return sha1(payload).hexdigest()


def _sample_ids(values: Iterable[int], limit: int = 20) -> List[int]:
    return list(sorted(values))[:limit]


def _print_summary(summary: Dict[str, Any], status: Dict[str, Any]) -> None:
    click.echo(
        "Portrait sync summary: "
        f"copied={summary['copied']}, "
        f"downloaded={summary['downloaded']}, "
        f"skipped={summary['skipped']}, "
        f"repaired={summary['repaired']}, "
        f"unresolved={summary['unresolved']}, "
        f"failed={summary['failed']}"
    )

    if summary['unresolved_ids']:
        click.echo(
            "Known unresolved IDs: " +
            ", ".join(map(str, _sample_ids(summary['unresolved_ids'])))
        )

    if summary['failed_ids']:
        click.echo(
            "Sync failures: " +
            ", ".join(map(str, _sample_ids(summary['failed_ids'])))
        )

    click.echo(status['summary'])


@click.command()
@click.option('--units',
              type=click.Path(dir_okay=False, exists=True, path_type=Path),
              default=DATA_DIR / 'units.json')
@click.option('--output',
              type=click.Path(file_okay=False, path_type=Path),
              default=DATA_DIR / 'Portraits')
@click.option('--team-builder-root',
              type=click.Path(file_okay=False, path_type=Path),
              default=None)
def main(units: Path, output: Path, team_builder_root: Optional[Path]):
    """Download or repair all portrait thumbnails required by the local units set."""
    output.mkdir(exist_ok=True, parents=True)

    if team_builder_root is None:
        team_builder_root = _default_team_builder_root()

    local_viable_ids = set(load_viable_unit_ids(units))
    manifest = _load_manifest()
    manifest_items = [
        item for item in manifest.get('items', [])
        if item['id'] in local_viable_ids
    ]
    manifest_items = _apply_team_builder_overrides(
        manifest_items,
        team_builder_root,
    )

    if not manifest_items:
        raise click.ClickException(
            "No viable units found for the local dataset."
        )

    results = thread_map(
        _sync_single_portrait,
        [(item, output, team_builder_root) for item in manifest_items],
        max_workers=max(4, mp.cpu_count() * 2),
        total=len(manifest_items),
    )

    summary = {
        'copied': 0,
        'downloaded': 0,
        'skipped': 0,
        'repaired': 0,
        'unresolved': 0,
        'failed': 0,
        'unresolved_ids': [],
        'failed_ids': [],
    }

    for result in results:
        summary[result['action']] += 1
        if result['repaired']:
            summary['repaired'] += 1
        if result['action'] == 'unresolved':
            summary['unresolved_ids'].append(result['id'])
        if result['action'] == 'failed':
            summary['failed_ids'].append(result['id'])

    _write_sync_report(
        REPORT_PATH,
        sorted(local_viable_ids),
        summary['unresolved_ids'],
        summary['failed_ids'],
        manifest,
        summary,
    )
    status = build_local_portrait_status(units, output, REPORT_PATH)
    _print_summary(summary, status)

    if summary['failed']:
        raise click.ClickException(
            f"Portrait sync finished with {summary['failed']} failed downloads."
        )


if __name__ == "__main__":
    main()
