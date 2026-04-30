# OPTC Box Exporter (OPTCbx)

Export your OPTC character box automatically from screenshots.

OPTCbx can analyze your character box screenshots (*Figure 1*), and obtain all the 
characters within them. OPTCbx does that without any manual intervention.

OPTCbx is the first box exporter for One Piece Treasure Cruise without any manual
intervention. It works with just a set of screenshots.

<div>
    <p align="center">
        <img style=" margin: auto; display: block" src="data/screenshots/Screenshot_20201014-155031.jpg" width=200/>
    </p>
    <p align="center" style="text-align: center;"><i>Figure 1: Box Screenshot</i></p>
</div>

By its own, OPTCbx, does not have any utility apart from showing your character box in a fancy manner. 

Power comes with contributions. I believe that OPTCbx can be a key point
combined with projects such as:

- [NakamaNetwork](https://www.nakama.network/): With OPTCbx you are not far 
from automatically exporting your teams or your box to this extraordinary website.

- [CrewPlanner](https://www.reddit.com/r/OnePieceTC/comments/j60ueg/crew_planner_is_now_available/): Imagine that you can export all your characters to CrewPlanner without spending hours introducing your characters one by one. With OPTCbx, you
will be able to do so with just a few minutes or even seconds 😲.

- [TM Planner](https://lukforce.bitbucket.io/tm-planner/): OPTCbx would provide the ability to automatically select the 
characters that you don't own.


## Local browser UI (Recommended)

This project is a Python web app for local use. It runs on your computer and you
open the UI in a browser.

All commands in this section assume your current terminal is already at the
project root (`optc-box-exporter`).

If you just want to run it from the repo root in VSCode terminal, use:

```bash
./tools/run-browser.sh
```

It will install what is needed, start the local server, and open:

```text
http://127.0.0.1:1234
```

To stop it later:

```bash
./tools/stop-browser.sh
```

Manual setup steps are below if you want to understand or control each step.

1. Install the dependencies

```bash
pip install -r requirements.txt
```

2. Refresh the OPTC units metadata from [OPTC DB](https://github.com/optc-db/optc-db.github.io) when you want the latest roster

```bash
sh ./tools/download-units.sh --source=optc-db
```

This step updates `data/units.json`. The default source is `optc-db`; pass
`--source=2shankz` only when you intentionally want to mirror that upstream
fork instead.

3. Sync the portraits used for matching

```bash
python -m optcbx download-portraits \
    --units data/units.json \
    --output data/Portraits \
    --source=optc-db
```

If you also have the sibling `optc-team-builder` repo next to this project, the
command automatically reuses its local `thumbnails-glo` cache. You can also pass
it explicitly:

```bash
python -m optcbx download-portraits \
    --units data/units.json \
    --output data/Portraits \
    --team-builder-root ../optc-team-builder \
    --source=optc-db
```

4. Start the local web server

```bash
python -m optcbx flask --debug
```

5. Open the browser UI

```text
http://127.0.0.1:1234
```

The page now shows a startup checklist. It validates portrait coverage against
the current local units dataset, not just file presence, and it reports missing
portrait gaps before export starts. Known upstream gaps are shown as partial
coverage warnings; browser export still runs for the portraits available
locally.

`DATABASE_URL` is now optional for local runs. Without it, the app still starts
normally and the feedback feature is simply disabled.

The browser export also includes optional filters for unit `type` and `class`.
Leaving them empty keeps the existing full-roster search.

- Types: `STR`, `DEX`, `QCK`, `PSY`, and `INT`
- Classes: `Booster`, `Cerebral`, `Driven`, `Evolver`, `Fighter`, `Free Spirit`,
  `Powerhouse`, `Shooter`, `Slasher`, and `Striker`

Selecting one or more values applies a strict portrait-candidate filter before
matching, which usually makes exports faster and more accurate when you already
know the type/class mix in the screenshot. When both filters are active, a
portrait must satisfy both groups.

The browser UI now also includes a separate **batch export** section:

- Upload many screenshots at once with a multi-file input.
- The UI analyzes them **sequentially** by reusing the same `POST /export`
  endpoint that powers the single-image export.
- The final batch output is one merged favorites JSON with the same schema as
  the single export:

```json
{
  "characters": [
    { "number": 101, "name": "Example unit" }
  ]
}
```

Batch merge behavior:

- Favorites are deduplicated by `number`.
- First-seen order is preserved across the whole batch.
- Failed screenshots are skipped and listed in the UI report.
- Auto-download uses the browser's normal download behavior, which usually means
  the default Downloads folder. The app does **not** write directly to an
  arbitrary filesystem path.

### Image size controls

In the browser export UI, **Image Size** is the resolution used for each
detected character crop before matching against the portrait database.

- Higher values can improve matching quality in some screenshots.
- Higher values also increase computation time and memory usage.

Available sizing modes:

- **Square (NxN)** via slider: from `32x32` to `256x256` with step `1`
  (for example `33x33`, `34x34`, etc.).
- **Custom (Width x Height)** via numeric fields: independent width and
  height, each between `32` and `256`.

API behavior for `POST /export`:

- Legacy `imageSize` (single number) is still supported.
- Optional `imageWidth` + `imageHeight` can be provided for custom sizing.
- When both `imageWidth` and `imageHeight` are present, they take precedence
  over `imageSize`.
- Optional `expectedCount` can be provided as a positive integer.
  When present, export compares detected characters against this value.
  A mismatch returns a warning in the success response (it does **not** fail the export).
- Optional `charactersPerRow` can be provided as a positive integer.
  When present, export uses this row size to sort detected crops before matching.
  This can improve ordering/matching on screenshots that do not follow the default `5` per row layout.
- Optional `manualGrid` can be provided as
  `{ "verticalLines": [x1, x2, ...], "horizontalLines": [y1, y2, ...] }`.
  When present, export crops slots from adjacent grid lines instead of using
  automatic split detection.

The browser single-image flow uses `POST /split-preview` before `POST /export`.
`/split-preview` accepts `{ "image": "<base64 screenshot>" }` and returns source
dimensions, detected grid lines, generated slot rectangles, slot count, and any
preview warnings. Batch export still calls `/export` directly.

`POST /export` response now also includes:

- `expectedCount`: provided expected value or `null` when not set.
- `detectedCount`: total detected/matched characters returned.
- `countMatch`: `true` when counts match (or when `expectedCount` is not set).
- `countWarning`: present only when `expectedCount` is set and counts differ.
- `charactersPerRow`: provided row-size value or `null` when not set.
- `rowCountMatch`: `true` when `detectedCount` is divisible by `charactersPerRow`
  (or when `charactersPerRow` is not set).
- `rowCountWarning`: present only when `charactersPerRow` is set and divisibility check fails.

Known upstream unresolved portrait IDs that need manual portraits for full OCR
coverage are currently:

```text
4204, 4206, 4207, 4208, 4209, 4210, 4214, 4215, 5602, 5603, 5604, 5605
```

## CLI demo (Legacy)

If you prefer the old OpenCV window demo, run:

```bash
python -m optcbx demo <screenshot-path>
```

The CLI demo is legacy code. It still needs extra AI assets from:

```bash
sh ./ai/prepare-ai.sh
```

> Note: If OpenCV shows warnings regarding png files, run `fix.bat` inside `tools`.

## Training skill (audit/fix loop)

When people refer to the "training skill" in this repository, they usually mean
the Codex skill `optc-box-audit-loop`. This is an **audit/fix loop**, not
neural network retraining.

Use it when you want an iterative workflow from chat-shared assets:

- first image = screenshot input
- second, third, and all remaining images = ordered output character images
- latest `Download favorites JSON` file
- corrected JSON with the order that should be considered canonical

The audit command now expects a strict case folder layout:

```text
<case-folder>/
  input/
    <exactly-one-screenshot>.png|jpg|jpeg|webp
  output/
    <one-or-more-output-character-images>.png|jpg|jpeg|webp
  meta/
    corrected.json
    optcbx-favorites-*.json or favorites*.json (optional, newest file auto-detected)
    notes.txt (optional)
```

Important ordering rule:

- `output/` images are ordered by **natural filename order** (for example
  `slot-1.png`, `slot-2.png`, `slot-10.png`).
- This order is treated as row-major expected order (left-to-right, top-to-bottom).

Practical staging flow from chat attachments:

1. Put the first image in `input/`.
2. Put every next result image in `output/`.
3. Put the corrected canonical JSON in `meta/corrected.json`.
4. Put the downloaded favorites file in `meta/` without renaming if you want.
5. Before each new run, clear old images in `output/` so stale files are not reused.

Run it with the skill helper:

```bash
~/.codex/skills/optc-box-audit-loop/scripts/run.sh "<case-folder>" [image-size]
```

Equivalent direct CLI command:

```bash
python -m optcbx audit-case "<case-folder>" --image-size 64 --write-artifacts
```

Generated artifacts (written in the same case folder):

- `actual-export.json`
- `actual-grid.html`
- `provided-export.json`
- `provided-grid.html`
- `audit-report.json`

Artifact meaning:

- `actual-*`: current rerun from `input/` using current repository code.
- `provided-*`: baseline matching of the already provided `output/` images.
- `audit-report.json`: combined report with:
  - `summary.status` based on `current` track only (main pass/fail signal)
  - `current` vs expected comparison
  - `provided` vs expected comparison
  - `favoritesChecks` (set-level consistency, not canonical ordering)

Practical loop:

1. Run audit on the case folder.
2. Inspect `audit-report.json`, `actual-grid.html`, and `provided-grid.html`.
3. Patch matcher/crop logic in this repo.
4. Rerun the same case until `summary.status` is `exact_match`.

Hard blockers to treat as data issues (not matcher code bugs):

- `expected_id_missing_from_local_units`
- `missing_portrait_asset`

For real model training (SSD / feature extractor), see `notebooks/` and
`ai/prepare-ai.sh`.

## How it works (Advanced users)

OPTCbx supports different computer vision techniques to retrieve the characters sitting on your box and match them with the corresponding [OPTC database](https://optc-db.github.io/characters/#/search/) entries.

Currently, OPTCbx supports 2 main approaches to detect and match the characters:

- **Gradient based approach**: Handcrafted steps based on the colors' change gradients.
- **Smart approach**: Based on object detection models and self-supervision to match characters.

The used technologies are:

- [OpenCV](https://opencv.org/)
- [NumPy](https://numpy.org/)
- [PyTorch](https://pytorch.org/)

### Gradient based approach - Step by step

1. **Retrieve character box portraits**: First keep only the high intensity colors such as white and yellow. Then with the resulting masked image I apply a canny edge detection (*Figure 2*) to obtain the character box borders.

<div>
    <p align="center">
        <img style=" margin: auto; display: block" src="images/canny.jpg" width=250/>
    </p>
    <p align="center" style="text-align: center;"><i>Figure 2: Canny edge detection result</i></p>
</div>

2. Then with the Canny result, I apply an approach called [Hough Lines Transform](https://docs.opencv.org/3.4/d9/db0/tutorial_hough_lines.html) to detect vertical and horizontal lines to draw the box grid.

3. Finally, with the grid I am able to find the boxes wrapping each character (**Figure 3**). Then with these regions, cropping the characters one by one is straightforward.

<div>
    <p align="center">
        <img style=" margin: auto; display: block" src="images/boxes.jpg" width=250/>
    </p>
    <p align="center" style="text-align: center;"><i>Figure 3: Boxes wrapping characters</i></p>
</div>

4. **Finding matching characters**: To do so I compute a pairwise Mean Squared Error (*Equation 1*) distance with all the characters found in your box with all the database entries. Finally I pick the smallest distance (*Figure 4*).

<div>
    <p align="center">
        <img style=" margin: auto; display: block" src="images/mse.svg" width=200/>
    </p>
    <p align="center" style="text-align: center;"><i>Equation 1: MSE</i></p>
</div>

5. **Results**

<div>
    <p align="center">
        <img style=" margin: auto !important; display: block !important" src="images/demo-out.png" width=500/>
    </p>
    <p align="center" style="text-align: center !important;"><i>Figure 4: Matched characters. For each column, the character appearing at the right is your character and the one at the left is the OPTC db entry (exported one)</i></p>
</div>


### Smart Approach - Step by step

1. **Object detection model**: Using an own [SSD implementation](https://github.com/Guillem96/ssd-pytorch) I train an
SSD model with a VGG-16 backbone to detect OPTC characters in any screen (Character box, crew build, pirate festival teams, etc.).

2. **Self-supervision to generate image features and character matching**: Ideally instead of comparing large images, we want to compare small vectors encoding
the most important features of the given images. To generate this features I use a pretrained CNN. Sadly an ImageNet fine-tuning is not
enough to generate feature vectors with high capacity of representation. Therefore, I self-supervise a Resnet-18 so it learns to
reconstruct One Piece Characters' portraits. Using this new pretrained model the resulting matches seems accurate.

> NOTE: The ones interested in the AI part I upload all the related code inside the `notebooks` directory.

to update characters run script

./tools/update_characters.sh

to run application safely and correctly

./tools/stop_run.sh
