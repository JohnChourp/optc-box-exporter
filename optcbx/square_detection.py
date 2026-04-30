from typing import Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import yaml

ImageSize = Union[Tuple[int, int], int]
DetectResults = Union[List[np.ndarray], Tuple[List[np.ndarray],
                                              List[np.ndarray]]]
ManualGrid = Dict[str, Sequence[Union[int, float]]]

CHARACTER_TRIM_FRACTIONS = {
    'top': 0.03,
    'right': 0.05,
    'bottom': 0.12,
    'left': 0.03,
}
DEFAULT_CHARACTERS_PER_ROW = 5
GRID_LINE_MERGE_TOLERANCE_FRACTION = 0.005
MIN_PREVIEW_SLOT_WARNING_COUNT = 3


def select_rgb_white_yellow(image: np.ndarray) -> np.ndarray:
    # white color mask
    lower = np.uint8([240, 240, 240])
    upper = np.uint8([255, 255, 255])
    white_mask = cv2.inRange(image, lower, upper)

    # yellow color mask
    lower = np.uint8([150, 150, 0])
    upper = np.uint8([255, 255, 255])
    yellow_mask = cv2.inRange(image, lower, upper)

    # combine the mask
    mask = cv2.bitwise_or(white_mask, yellow_mask)
    masked = cv2.bitwise_and(image, image, mask=yellow_mask)
    return masked


def draw_lines(image: np.ndarray, lines: np.ndarray) -> np.ndarray:
    heights = np.abs(lines[:, 3] - lines[:, 1])
    widths = np.abs(lines[:, 2] - lines[:, 0])

    h_lines_mask = (heights <= 1) & (widths > image.shape[1] * .05)
    v_lines_mask = (widths <= 1) & (heights > image.shape[0] * .05)
    valid_mask = h_lines_mask | v_lines_mask

    # Extend horizontal lines to fit the whole image
    lines[h_lines_mask, 0] = 0
    lines[h_lines_mask, 2] = image.shape[1]

    # Extend vertical lines to fit the whole image
    lines[v_lines_mask, 1] = 0
    lines[v_lines_mask, 3] = image.shape[0]

    for x1, y1, x2, y2 in lines[valid_mask]:
        cv2.line(image, (x1, y1), (x2, y2), [255, 255, 255], 2)

    return image


def trim_character_crop(image: np.ndarray) -> np.ndarray:
    if image.size == 0:
        return image

    height, width = image.shape[:2]

    top = int(round(height * CHARACTER_TRIM_FRACTIONS['top']))
    right = int(round(width * CHARACTER_TRIM_FRACTIONS['right']))
    bottom = int(round(height * CHARACTER_TRIM_FRACTIONS['bottom']))
    left = int(round(width * CHARACTER_TRIM_FRACTIONS['left']))

    y1 = min(max(top, 0), max(height - 2, 0))
    x1 = min(max(left, 0), max(width - 2, 0))
    y2 = max(y1 + 1, height - max(bottom, 0))
    x2 = max(x1 + 1, width - max(right, 0))

    if y2 <= y1 or x2 <= x1:
        return image

    trimmed = image[y1:y2, x1:x2]
    return trimmed if trimmed.size else image


def detect_characters(image: Union[str, np.ndarray],
                      characters_size: Optional[ImageSize] = None,
                      screen: str = 'character_box',
                      approach: str = 'smart',
                      characters_per_row: Optional[int] = None,
                      return_rectangles: bool = False) -> DetectResults:

    if approach not in {'smart', 'gradient_based'}:
        raise ValueError("We only support 'smart' or 'gradient_based'"
                         f"approaches, and you provided {approach}")

    if screen not in {'character_box'}:
        raise ValueError("We only support character detection in {}"
                         " and you provided {}".format({'character_box'},
                                                       screen))

    if approach == 'gradient_based':
        return _gradient_based_approach(image, characters_size, screen,
                                        return_rectangles,
                                        characters_per_row=characters_per_row)
    elif approach == 'smart':
        return _smart_approach(image, characters_size, return_rectangles)


def detect_split_preview(image: Union[str, np.ndarray],
                         approach: str = 'gradient_based') -> Dict[str, object]:
    if approach != 'gradient_based':
        raise ValueError("Split preview only supports the 'gradient_based' approach.")

    if isinstance(image, str):
        image = cv2.imread(image)

    rects = _detect_gradient_rectangles(image)
    vertical_lines, horizontal_lines = _grid_lines_from_rectangles(rects, image.shape)
    slot_rects = _rectangles_from_grid_lines(vertical_lines, horizontal_lines)
    warnings = []

    if len(rects) == 0:
        warnings.append("No character slots were detected automatically.")
    elif len(slot_rects) < MIN_PREVIEW_SLOT_WARNING_COUNT:
        warnings.append(
            f"Only {len(slot_rects)} character slot(s) were detected automatically."
        )

    return {
        "imageWidth": int(image.shape[1]),
        "imageHeight": int(image.shape[0]),
        "verticalLines": vertical_lines,
        "horizontalLines": horizontal_lines,
        "rectangles": slot_rects,
        "slotCount": len(slot_rects),
        "warnings": warnings,
    }


def detect_characters_from_manual_grid(
        image: Union[str, np.ndarray],
        manual_grid: ManualGrid,
        characters_size: Optional[ImageSize] = None) -> Tuple[np.ndarray, np.ndarray]:
    if isinstance(characters_size, int):
        characters_size = (characters_size, ) * 2

    if isinstance(image, str):
        image = cv2.imread(image)

    normalized_grid = normalize_manual_grid(manual_grid, image.shape)
    valid_rects = _xyxy_to_xywh(_grid_rectangles_to_array(
        _rectangles_from_grid_lines(
            normalized_grid["verticalLines"],
            normalized_grid["horizontalLines"],
        )
    ))
    return _build_character_crops_from_rectangles(image, valid_rects, characters_size)


def normalize_manual_grid(manual_grid: ManualGrid,
                          image_shape: Tuple[int, ...]) -> Dict[str, List[int]]:
    if not isinstance(manual_grid, dict):
        raise ValueError("manualGrid must be an object with verticalLines and horizontalLines.")

    width = int(image_shape[1])
    height = int(image_shape[0])

    return {
        "verticalLines": _normalize_grid_lines(
            manual_grid.get("verticalLines"),
            "manualGrid.verticalLines",
            width,
        ),
        "horizontalLines": _normalize_grid_lines(
            manual_grid.get("horizontalLines"),
            "manualGrid.horizontalLines",
            height,
        ),
    }


# Again, this is for efficiency, global variables usually suck
_model = None


def _smart_approach(image: Union[str, np.ndarray],
                    characters_size: Optional[ImageSize] = None,
                    return_rectangles: bool = False):
    global _model, _config

    import ssd
    import ssd.transforms as T
    import torch

    device = torch.device('cpu')
    tfms = T.get_transforms(300, inference=True)

    if isinstance(characters_size, int):
        characters_size = (characters_size, ) * 2

    if isinstance(image, str):
        image = cv2.imread(image)

    if _model is None:
        config = yaml.safe_load(open('ai/config.yml'))['config']

        _model = ssd.SSD300(config)
        _model.eval()

        checkpoint = torch.load('ai/checkpoint.pt', map_location=device)
        _model.load_state_dict(checkpoint)
        _model.to(device)

    im_in = tfms(image)
    im_in = im_in.unsqueeze(0).to(device)

    scale = torch.as_tensor([image.shape[1], image.shape[0]] * 2)
    scale.unsqueeze_(0)

    with torch.no_grad():
        detections = _model(im_in)[0]

    true_mask = detections['scores'] > .5
    boxes = (detections['boxes'][true_mask].cpu() * scale).int().numpy()
    characters = [
        image[y_min:y_max, x_min:x_max] for x_min, y_min, x_max, y_max in boxes
    ]

    if characters_size is not None:
        characters = np.array(
            [cv2.resize(o, characters_size) for o in characters])

    if not return_rectangles:
        return characters
    else:
        return characters, boxes


def _gradient_based_approach(image: Union[str, np.ndarray],
                             characters_size: Optional[ImageSize] = None,
                             screen: str = 'character_box',
                             return_rectangles: bool = False,
                             characters_per_row: Optional[int] = None):

    if isinstance(characters_size, int):
        characters_size = (characters_size, ) * 2

    if isinstance(image, str):
        image = cv2.imread(image)

    valid_rects = _detect_gradient_rectangles(image)
    characters, valid_rects = _build_character_crops_from_rectangles(
        image,
        valid_rects,
        characters_size,
    )

    if screen == 'character_box' and characters_size is not None:
        characters, valid_rects = _sort_detected_characters_by_grid(
            characters,
            valid_rects,
            characters_per_row=characters_per_row,
        )

    if not return_rectangles:
        return characters
    else:
        return characters, _xywh_to_xyxy(valid_rects)


def _detect_gradient_rectangles(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        return np.empty((0, 4), dtype='int32')

    # Retrieve yellows and whites from the image
    res = select_rgb_white_yellow(image)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
    res = cv2.filter2D(res, -1, kernel)

    # Convert the image to gray scale and apply a canny edge detection
    res = cv2.cvtColor(res, cv2.COLOR_BGR2GRAY)
    res = cv2.Canny(res, 50, 255)

    # Based on the canny edge detection result, find straight lines in the image
    # TODO: Better understand parameters: rho and maxLineGap
    lines = cv2.HoughLinesP(res,
                            rho=.1,
                            theta=np.pi / 10.,
                            threshold=150,
                            minLineLength=5,
                            maxLineGap=4)
    if lines is None:
        return np.empty((0, 4), dtype='int32')

    lines = lines.reshape(-1, 4)

    # Draw completely horizontal and vertical lines and expand them to fit the
    # image, this way we can build a grid spliting all the box characters
    res = draw_lines(cv2.cvtColor(res, cv2.COLOR_GRAY2BGR), lines)

    # Again convert the image to gray scale and threshold it in order to
    # binarize it
    res = cv2.cvtColor(res, cv2.COLOR_BGR2GRAY)
    res = cv2.threshold(res, 200, 255, cv2.THRESH_BINARY)[1]

    # With the binarized image, we retrieve the countours
    cnts = cv2.findContours(res, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    cnts = cnts[0] if len(cnts) == 2 else cnts[1]

    if not cnts:
        return np.empty((0, 4), dtype='int32')

    # Get the rectangles surrounding the countors and compute its area and aspect
    # ratio
    rects = np.array([cv2.boundingRect(o) for o in cnts], dtype='float32')
    areas = rects[:, 2] * rects[:, 3]
    ar = rects[:, 3] / rects[:, 2]

    # We filter out:
    #   - the rectangles which have a tiny area (1% image area)
    #   - The rectangles with a massive area (15% image area)
    #   - The rectangles which do not have a squared shape (aspect ratio near to 1)
    min_area = .01 * (image.shape[1] * image.shape[0])
    max_area = .15 * (image.shape[1] * image.shape[0])
    valid_rectangles = (areas > min_area) & (areas < max_area)
    valid_rectangles &= (ar > .8) & (ar < 1.2)

    return rects[valid_rectangles].astype('int32')


def _extract_valid_character_crops(image: np.ndarray, rects: np.ndarray):
    valid_rects = []
    characters = []
    height, width = image.shape[:2]

    for x, y, w, h in rects:
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(width, x + w)
        y2 = min(height, y + h)

        if x2 <= x1 or y2 <= y1:
            continue

        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            continue

        valid_rects.append([x1, y1, x2 - x1, y2 - y1])
        characters.append(crop)

    return np.asarray(valid_rects, dtype='int32'), characters


def _build_character_crops_from_rectangles(
        image: np.ndarray,
        rects: np.ndarray,
        characters_size: Optional[Tuple[int, int]] = None):
    valid_rects, characters = _extract_valid_character_crops(image, rects)
    characters = [trim_character_crop(o) for o in characters]

    if characters_size is None:
        if len(characters) == 0:
            return np.empty((0, 0, 0, 3), dtype='uint8'), valid_rects
        return characters, valid_rects

    if len(characters) == 0:
        return np.empty((0, *characters_size, 3), dtype='uint8'), valid_rects

    return np.array([cv2.resize(o, characters_size) for o in characters]), valid_rects


def _xywh_to_xyxy(rects: np.ndarray) -> np.ndarray:
    if rects.size == 0:
        return np.empty((0, 4), dtype='int32')

    xyxy = rects.copy()
    xyxy[..., 2] = xyxy[..., 0] + xyxy[..., 2]
    xyxy[..., 3] = xyxy[..., 1] + xyxy[..., 3]
    return xyxy.astype('int32')


def _xyxy_to_xywh(rects: np.ndarray) -> np.ndarray:
    if rects.size == 0:
        return np.empty((0, 4), dtype='int32')

    xywh = rects.copy()
    xywh[..., 2] = xywh[..., 2] - xywh[..., 0]
    xywh[..., 3] = xywh[..., 3] - xywh[..., 1]
    return xywh.astype('int32')


def _grid_lines_from_rectangles(
        rects: np.ndarray,
        image_shape: Tuple[int, ...]) -> Tuple[List[int], List[int]]:
    if rects.size == 0:
        return [], []

    xyxy = _xywh_to_xyxy(rects)
    tolerance = max(
        2,
        int(round(min(int(image_shape[0]), int(image_shape[1])) *
                  GRID_LINE_MERGE_TOLERANCE_FRACTION)),
    )
    vertical_lines = _merge_nearby_grid_lines(
        np.concatenate([xyxy[:, 0], xyxy[:, 2]]),
        tolerance,
        int(image_shape[1]),
    )
    horizontal_lines = _merge_nearby_grid_lines(
        np.concatenate([xyxy[:, 1], xyxy[:, 3]]),
        tolerance,
        int(image_shape[0]),
    )
    return vertical_lines, horizontal_lines


def _merge_nearby_grid_lines(values: np.ndarray,
                             tolerance: int,
                             max_value: int) -> List[int]:
    if values.size == 0:
        return []

    sorted_values = sorted(int(round(value)) for value in values)
    clusters: List[List[int]] = []

    for value in sorted_values:
        value = min(max(value, 0), max_value)
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)

    return [
        min(max(int(round(sum(cluster) / len(cluster))), 0), max_value)
        for cluster in clusters
    ]


def _rectangles_from_grid_lines(vertical_lines: Sequence[int],
                                horizontal_lines: Sequence[int]) -> List[Dict[str, int]]:
    rectangles = []

    for row_index in range(max(len(horizontal_lines) - 1, 0)):
        y1 = int(horizontal_lines[row_index])
        y2 = int(horizontal_lines[row_index + 1])
        if y2 <= y1:
            continue

        for column_index in range(max(len(vertical_lines) - 1, 0)):
            x1 = int(vertical_lines[column_index])
            x2 = int(vertical_lines[column_index + 1])
            if x2 <= x1:
                continue

            rectangles.append({
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "row": row_index,
                "column": column_index,
            })

    return rectangles


def _grid_rectangles_to_array(rectangles: Sequence[Dict[str, int]]) -> np.ndarray:
    if not rectangles:
        return np.empty((0, 4), dtype='int32')

    return np.asarray([
        [rect["x1"], rect["y1"], rect["x2"], rect["y2"]]
        for rect in rectangles
    ], dtype='int32')


def _normalize_grid_lines(raw_lines,
                          field_name: str,
                          max_value: int) -> List[int]:
    if not isinstance(raw_lines, (list, tuple)):
        raise ValueError(f"{field_name} must be an array of numeric values.")

    normalized = []
    for raw_line in raw_lines:
        if isinstance(raw_line, bool) or not isinstance(raw_line, (int, float)):
            raise ValueError(f"{field_name} must contain only numeric values.")
        if not np.isfinite(raw_line):
            raise ValueError(f"{field_name} must contain only finite numeric values.")

        value = int(round(raw_line))
        if value < 0 or value > max_value:
            raise ValueError(f"{field_name} values must be within the source image bounds.")
        normalized.append(value)

    normalized = sorted(set(normalized))
    if len(normalized) < 2:
        raise ValueError(f"{field_name} must contain at least two unique values.")

    return normalized


def _resolve_characters_per_row(characters_per_row: Optional[int]) -> int:
    if characters_per_row is None:
        return DEFAULT_CHARACTERS_PER_ROW
    if isinstance(characters_per_row, bool) or not isinstance(characters_per_row, int):
        raise ValueError("characters_per_row must be a positive integer.")
    if characters_per_row <= 0:
        raise ValueError("characters_per_row must be a positive integer.")
    return characters_per_row


def _sort_detected_characters_by_grid(
        characters: np.ndarray,
        valid_rects: np.ndarray,
        characters_per_row: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
    row_size = _resolve_characters_per_row(characters_per_row)

    if characters.shape[0] == 0:
        return characters, valid_rects

    pad = characters.shape[0] % row_size
    if pad > 0:
        pad = row_size - pad
        characters = np.pad(characters, ((0, pad), (0, 0), (0, 0), (0, 0)))
        valid_rects = np.pad(valid_rects, ((0, pad), (0, 0)), constant_values=9999)

    sort_by_y_idx = np.argsort(valid_rects[:, 1])
    characters = characters[sort_by_y_idx]
    valid_rects = valid_rects[sort_by_y_idx]

    characters = characters.reshape(-1, row_size, *characters.shape[1:])
    valid_rects = valid_rects.reshape(-1, row_size, 4)

    sort_by_x_idx = np.argsort(valid_rects[..., 0], axis=1)
    indexer = np.arange(characters.shape[0]).reshape(-1, 1)
    characters = characters[indexer, sort_by_x_idx]
    valid_rects = valid_rects[indexer, sort_by_x_idx]

    characters = characters.reshape((-1, *characters.shape[2:]))
    valid_rects = valid_rects.reshape(-1, 4)

    if pad > 0:
        characters = characters[:characters.shape[0] - pad]
        valid_rects = valid_rects[:valid_rects.shape[0] - pad]

    return characters, valid_rects


if __name__ == "__main__":
    characters = detect_characters('test-crop.jpg')
    print("Characters found:", len(characters))
