import io
import os
import string
import base64
import random
import binascii
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import numpy as np
from PIL import Image, UnidentifiedImageError

try:
    import psycopg2
except ImportError:
    psycopg2 = None

import optcbx
from optcbx.data.download_portraits import build_local_portrait_status

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

DATA_DIR = Path('data')
AI_DIR = Path('ai')
SCREENSHOTS_DIR = Path('.runtime') / 'screenshots'

RUNTIME_REQUIREMENTS = [
    {
        "key": "units",
        "label": "Units metadata",
        "path": DATA_DIR / 'units.json',
        "path_display": 'data/units.json',
        "kind": "file",
        "required_for": "Browser UI + CLI demo",
        "help": "Refresh with tools/download-units.sh if you need newer OPTC data."
    },
    {
        "key": "portraits",
        "label": "Portrait images",
        "path": DATA_DIR / 'Portraits',
        "path_display": 'data/Portraits/*.png',
        "kind": "glob",
        "pattern": '*.png',
        "required_for": "Browser UI + CLI demo",
        "help": ("Run `python -m optcbx download-portraits --units data/units.json "
                  "--output data/Portraits [--team-builder-root ../optc-team-builder]`.")
    },
    {
        "key": "detector_config",
        "label": "Legacy smart detector config",
        "path": AI_DIR / 'config.yml',
        "path_display": 'ai/config.yml',
        "kind": "file",
        "required_for": "Legacy CLI demo only",
        "help": "Run `cd ai && sh prepare-ai.sh && cd ..` if you want the old smart detector."
    },
    {
        "key": "detector_checkpoint",
        "label": "Legacy smart detector checkpoint",
        "path": AI_DIR / 'checkpoint.pt',
        "path_display": 'ai/checkpoint.pt',
        "kind": "file",
        "required_for": "Legacy CLI demo only",
        "help": "Run `cd ai && sh prepare-ai.sh && cd ..` if you want the old smart detector."
    },
    {
        "key": "feature_extractor",
        "label": "CLI feature extractor",
        "path": AI_DIR / 'fe.pt',
        "path_display": 'ai/fe.pt',
        "kind": "file",
        "required_for": "CLI demo only",
        "help": "Needed only for `python -m optcbx demo`."
    },
    {
        "key": "portrait_features",
        "label": "CLI portrait features",
        "path": AI_DIR / 'fv-portraits.pt',
        "path_display": 'ai/fv-portraits.pt',
        "kind": "file",
        "required_for": "CLI demo only",
        "help": "Needed only for `python -m optcbx demo`."
    }
]

WEB_REQUIRED_KEYS = {
    'units', 'portraits'
}
CLI_REQUIRED_KEYS = WEB_REQUIRED_KEYS | {
    'detector_config',
    'detector_checkpoint',
    'feature_extractor',
    'portrait_features'
}
SUPPORTED_TYPES = optcbx.SUPPORTED_TYPES
SUPPORTED_CLASSES = optcbx.SUPPORTED_CLASSES
IMAGE_SIZE_MIN = 32
IMAGE_SIZE_MAX = 256
DEFAULT_IMAGE_SIZE = 64


def _init_feedback_connection():
    database_url = os.environ.get('DATABASE_URL')

    if psycopg2 is None:
        return None, "Feedback storage is disabled because psycopg2 is not installed."

    if not database_url:
        return None, "Feedback storage is disabled for local runs because DATABASE_URL is not set."

    try:
        result = urlparse(database_url)
        connection = psycopg2.connect(database=result.path[1:],
                                      user=result.username,
                                      password=result.password,
                                      host=result.hostname)
        return connection, "Feedback storage is enabled."
    except Exception as exc:
        print(str(exc))
        return None, f"Feedback storage is disabled: {exc}"


connection, feedback_status = _init_feedback_connection()


def _build_runtime_status():
    checks = []
    portrait_status = build_local_portrait_status(
        DATA_DIR / 'units.json',
        DATA_DIR / 'Portraits',
    )

    for requirement in RUNTIME_REQUIREMENTS:
        path = requirement["path"]
        kind = requirement["kind"]
        help_text = requirement["help"]
        details = None

        if requirement["key"] == 'portraits':
            available = portrait_status["ready"]
            details = portrait_status
            help_text = f"{portrait_status['summary']} {help_text}"
        elif kind == 'glob':
            available = path.exists() and any(path.glob(requirement["pattern"]))
        else:
            available = path.exists()

        checks.append({
            "key": requirement["key"],
            "label": requirement["label"],
            "path": requirement["path_display"],
            "available": available,
            "required_for": requirement["required_for"],
            "help": help_text,
            "details": details,
            "status_label": (
                "Ready" if available else
                ("Needs sync" if requirement["key"] == 'portraits' else "Missing")
            ),
        })

    availability = {item["key"]: item["available"] for item in checks}
    missing_web = [
        item for item in checks if item["key"] in WEB_REQUIRED_KEYS and not item["available"]
    ]
    missing_cli = [
        item for item in checks if item["key"] in CLI_REQUIRED_KEYS and not item["available"]
    ]

    return {
        "checks": checks,
        "web_ready": all(availability[key] for key in WEB_REQUIRED_KEYS),
        "cli_demo_ready": all(availability[key] for key in CLI_REQUIRED_KEYS),
        "missing_web_requirements": missing_web,
        "missing_cli_requirements": missing_cli,
        "feedback_enabled": connection is not None,
        "feedback_status": feedback_status,
        "portrait_status": portrait_status,
    }


@app.route('/')
def index():
    return render_template(
        "index.html",
        runtime=_build_runtime_status(),
        supported_types=SUPPORTED_TYPES,
        supported_classes=SUPPORTED_CLASSES,
    )


@app.route('/runtime-status')
def runtime_status():
    return jsonify(_build_runtime_status())


@app.route('/feedback', methods=['POST'])
def feedback():
    if connection is None:
        return {
            "message": "Feedback storage is disabled for this local run."
        }, 200

    fb = request.json["fb"]
    try:
        cursor = connection.cursor()
        cursor.execute("INSERT INTO feedback(fb) VALUES (%s)", (fb,))
        connection.commit()
    except Exception as e:
        print(str(e))
        return {"message": str(e)}, 500

    return {"message": "thanks for the feedback"}, 200


@app.route('/export', methods=['POST'])
def export():
    payload = request.get_json(silent=True) or {}
    b64_image = payload.get("image")
    return_thumbnails = payload.get("returnThumbnails", False)

    try:
        allowed_types = optcbx.normalize_allowed_types(payload.get("types"))
    except ValueError as exc:
        return {
            "message": str(exc),
            "appliedTypes": [],
            "appliedClasses": [],
        }, 400

    try:
        allowed_classes = optcbx.normalize_allowed_classes(payload.get("classes"))
    except ValueError as exc:
        return {
            "message": str(exc),
            "appliedTypes": list(allowed_types),
            "appliedClasses": [],
        }, 400

    try:
        im_size = _parse_image_size(payload)
    except ValueError as exc:
        return {
            "message": str(exc),
            "appliedTypes": list(allowed_types),
            "appliedClasses": list(allowed_classes),
        }, 400

    try:
        expected_count = _parse_expected_count(payload)
    except ValueError as exc:
        return {
            "message": str(exc),
            "appliedTypes": list(allowed_types),
            "appliedClasses": list(allowed_classes),
        }, 400

    try:
        characters_per_row = _parse_characters_per_row(payload)
    except ValueError as exc:
        return {
            "message": str(exc),
            "appliedTypes": list(allowed_types),
            "appliedClasses": list(allowed_classes),
        }, 400

    if not b64_image:
        return {"message": "Missing screenshot payload."}, 400

    runtime = _build_runtime_status()
    if not runtime["web_ready"]:
        return jsonify({
            "message": ("Local browser export is not ready yet. Complete the missing "
                        "setup items shown on the page and retry."),
            "runtime": runtime
        }), 400

    try:
        image_bytes = base64.b64decode(b64_image.encode())
        im = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except (binascii.Error, UnidentifiedImageError, ValueError):
        return {"message": "Invalid screenshot payload."}, 400

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    im.save(_random_name())

    im = np.flip(np.array(im), -1).copy()

    try:
        if return_thumbnails:
            characters, thumbnails = optcbx.find_characters_from_screenshot(
                im,
                im_size,
                return_thumbnails=True,
                approach='gradient_based',
                allowed_types=allowed_types,
                allowed_classes=allowed_classes,
                characters_per_row=characters_per_row,
            )
            thumbnails = np.flip(thumbnails, -1)

            if len(characters) == 0:
                return {
                    "message": _build_no_detection_message(allowed_types, allowed_classes),
                    "appliedTypes": list(allowed_types),
                    "appliedClasses": list(allowed_classes),
                }, 422

            response = {
                "characters": [dict(o._asdict()) for o in characters],
                "thumbnails": [_img_to_b64(o) for o in thumbnails],
                "appliedTypes": list(allowed_types),
                "appliedClasses": list(allowed_classes),
                **_build_count_metadata(expected_count, len(characters)),
                **_build_row_metadata(characters_per_row, len(characters)),
            }
        else:
            characters = optcbx.find_characters_from_screenshot(
                im,
                im_size,
                return_thumbnails=False,
                approach='gradient_based',
                allowed_types=allowed_types,
                allowed_classes=allowed_classes,
                characters_per_row=characters_per_row,
            )

            if len(characters) == 0:
                return {
                    "message": _build_no_detection_message(allowed_types, allowed_classes),
                    "appliedTypes": list(allowed_types),
                    "appliedClasses": list(allowed_classes),
                }, 422

            response = {
                "characters": [dict(o._asdict()) for o in characters],
                "appliedTypes": list(allowed_types),
                "appliedClasses": list(allowed_classes),
                **_build_count_metadata(expected_count, len(characters)),
                **_build_row_metadata(characters_per_row, len(characters)),
            }
    except FileNotFoundError as exc:
        runtime = _build_runtime_status()
        return jsonify({
            "message": f"Missing runtime asset: {exc}",
            "runtime": runtime
        }), 400
    except optcbx.NoMatchingPortraitCandidatesError as exc:
        return {
            "message": str(exc),
            "appliedTypes": list(allowed_types),
            "appliedClasses": list(allowed_classes),
        }, 422
    except Exception as exc:
        print(str(exc))
        return {"message": f"Export failed: {exc}"}, 500

    return jsonify(response)


def _build_no_detection_message(allowed_types, allowed_classes):
    message = ("No OPTC portraits were detected in this screenshot. "
               "Try a clear character box screenshot.")
    active_filters = []
    if allowed_types:
        active_filters.append(f"types={', '.join(allowed_types)}")
    if allowed_classes:
        active_filters.append(f"classes={', '.join(allowed_classes)}")

    if active_filters:
        return message + " Active filters: " + "; ".join(active_filters) + "."
    return message


def _parse_image_size(payload):
    has_custom_width = "imageWidth" in payload and payload.get("imageWidth") is not None
    has_custom_height = "imageHeight" in payload and payload.get("imageHeight") is not None

    if has_custom_width or has_custom_height:
        if not (has_custom_width and has_custom_height):
            raise ValueError(
                "Both imageWidth and imageHeight are required when using custom image size."
            )

        width = _coerce_image_size_value(payload.get("imageWidth"), "imageWidth")
        height = _coerce_image_size_value(payload.get("imageHeight"), "imageHeight")
        _validate_image_size_range(width, "imageWidth")
        _validate_image_size_range(height, "imageHeight")
        return (width, height)

    size = _coerce_image_size_value(payload.get("imageSize", DEFAULT_IMAGE_SIZE), "imageSize")
    _validate_image_size_range(size, "imageSize")
    return size


def _coerce_image_size_value(raw_value, field_name):
    if isinstance(raw_value, bool):
        raise ValueError(f"{field_name} must be an integer between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}.")

    if isinstance(raw_value, int):
        return raw_value

    if isinstance(raw_value, float):
        if raw_value.is_integer():
            return int(raw_value)
        raise ValueError(f"{field_name} must be an integer between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}.")

    if isinstance(raw_value, str):
        value = raw_value.strip()
        if value and value.lstrip("-").isdigit():
            return int(value)
        raise ValueError(f"{field_name} must be an integer between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}.")

    raise ValueError(f"{field_name} must be an integer between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}.")


def _validate_image_size_range(value, field_name):
    if value < IMAGE_SIZE_MIN or value > IMAGE_SIZE_MAX:
        raise ValueError(
            f"{field_name} must be between {IMAGE_SIZE_MIN} and {IMAGE_SIZE_MAX}."
        )


def _parse_expected_count(payload):
    if "expectedCount" not in payload:
        return None

    raw_value = payload.get("expectedCount")
    if raw_value is None:
        return None
    if isinstance(raw_value, str) and raw_value.strip() == "":
        return None
    if isinstance(raw_value, bool):
        raise ValueError("expectedCount must be a positive integer.")

    if isinstance(raw_value, int):
        value = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise ValueError("expectedCount must be a positive integer.")
        value = int(raw_value)
    elif isinstance(raw_value, str):
        value_str = raw_value.strip()
        if not value_str.lstrip("-").isdigit():
            raise ValueError("expectedCount must be a positive integer.")
        value = int(value_str)
    else:
        raise ValueError("expectedCount must be a positive integer.")

    if value <= 0:
        raise ValueError("expectedCount must be a positive integer.")

    return value


def _parse_characters_per_row(payload):
    if "charactersPerRow" not in payload:
        return None

    raw_value = payload.get("charactersPerRow")
    if raw_value is None:
        return None
    if isinstance(raw_value, str) and raw_value.strip() == "":
        return None
    if isinstance(raw_value, bool):
        raise ValueError("charactersPerRow must be a positive integer.")

    if isinstance(raw_value, int):
        value = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise ValueError("charactersPerRow must be a positive integer.")
        value = int(raw_value)
    elif isinstance(raw_value, str):
        value_str = raw_value.strip()
        if not value_str.lstrip("-").isdigit():
            raise ValueError("charactersPerRow must be a positive integer.")
        value = int(value_str)
    else:
        raise ValueError("charactersPerRow must be a positive integer.")

    if value <= 0:
        raise ValueError("charactersPerRow must be a positive integer.")

    return value


def _build_count_metadata(expected_count, detected_count):
    count_match = expected_count is None or detected_count == expected_count
    metadata = {
        "expectedCount": expected_count,
        "detectedCount": detected_count,
        "countMatch": count_match,
    }

    if expected_count is not None and not count_match:
        metadata["countWarning"] = (
            f"Detected {detected_count} characters, but expected {expected_count}. "
            "Review the screenshot and filters, then retry if needed."
        )

    return metadata


def _build_row_metadata(characters_per_row, detected_count):
    row_count_match = (
        characters_per_row is None or
        detected_count % characters_per_row == 0
    )
    metadata = {
        "charactersPerRow": characters_per_row,
        "rowCountMatch": row_count_match,
    }

    if characters_per_row is not None and not row_count_match:
        metadata["rowCountWarning"] = (
            f"Detected {detected_count} characters, which is not divisible by "
            f"charactersPerRow={characters_per_row}. Review the value and retry if needed."
        )

    return metadata


def _img_to_b64(im):
    im = Image.fromarray(im)
    buffered = io.BytesIO()
    im.save(buffered, format="JPEG")
    return ("data:image/jpeg;base64," +
            base64.b64encode(buffered.getvalue()).decode())


def _random_name():
    ln = string.ascii_letters + string.digits
    name = ''.join([random.choice(ln) for _ in range(20)]) + '.jpg'
    return str(SCREENSHOTS_DIR / name)
