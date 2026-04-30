var currentScreenshotB64 = '';
var currentScreenshotDataUrl = '';
var splitPreviewState = null;
var selectedGridLine = null;
var activeGridDrag = null;
var lastExportResponse = null;
var areNamesVisible = true;
var isBatchRunning = false;
var lastBatchFavoritesPayload = null;
var batchExportState = null;
const IMAGE_SIZE_MIN = 32;
const IMAGE_SIZE_MAX = 256;
const MAX_RENDERED_BATCH_FAILURES = 200;

$(document).ready(function () {
    $('#sidebarCollapse').on('click', function () {
        $('#sidebar').toggleClass('active');
    });

    $('#image-size-slider').on('input change', updateImageSizeDisplay);
    $('input[name="image-size-mode"]').on('change', updateImageSizeControls);
    $('#image-width-input, #image-height-input').on('input change', updateImageSizeDisplay);

    $('#screenshot-file').change(handleSingleScreenshotSelection);
    $('#batch-screenshot-files').change(handleBatchScreenshotSelection);
    $('#add-vertical-line-btn').on('click', function () { addGridLine('vertical'); });
    $('#add-horizontal-line-btn').on('click', function () { addGridLine('horizontal'); });
    $('#remove-grid-line-btn').on('click', removeSelectedGridLine);
    $('#reset-grid-lines-btn').on('click', resetGridLinesToDetected);

    $(document).on('mousemove touchmove', handleGridDragMove);
    $(document).on('mouseup touchend touchcancel', endGridDrag);

    updateImageSizeControls();
    updateSingleExportButtonState();
    updateGridControlsState();
    updateBatchSelectionSummary();
    updateBatchExportButtonState();
    disableBatchDownload();
    initializeUnresolvedIdBrowsers();
    $('.loading-wrapper').hide();
});

function initializeUnresolvedIdBrowsers() {
    $('.unresolved-id-browser').each(function () {
        const container = $(this);
        const ids = parseUnresolvedIds(container.attr('data-unresolved-ids'));
        const state = {
            ids,
            visibleCount: Math.min(10, ids.length)
        };

        container.data('unresolvedState', state);
        container.find('.unresolved-id-browser__load-more').on('click', function () {
            state.visibleCount = Math.min(state.visibleCount + 10, state.ids.length);
            renderUnresolvedIdBrowser(container);
        });
        renderUnresolvedIdBrowser(container);
    });
}

function parseUnresolvedIds(rawValue) {
    try {
        const parsed = JSON.parse(rawValue || '[]');
        return Array.isArray(parsed)
            ? parsed.filter(value => Number.isInteger(Number(value))).map(value => Number(value))
            : [];
    } catch (error) {
        return [];
    }
}

function renderUnresolvedIdBrowser(container) {
    const state = container.data('unresolvedState') || { ids: [], visibleCount: 0 };
    const visibleIds = state.ids.slice(0, state.visibleCount);

    container.find('.unresolved-id-browser__list').html(
        visibleIds.map(id => `<code class="unresolved-id-browser__chip">${id}</code>`).join('')
    );

    const remainingCount = state.ids.length - state.visibleCount;
    const button = container.find('.unresolved-id-browser__load-more');
    if (remainingCount > 0) {
        button
            .removeClass('d-none')
            .text(`Load more (${remainingCount} remaining)`);
    } else {
        button.addClass('d-none');
    }
}

function handleSingleScreenshotSelection() {
    var file = this.files[0];
    var reader = new FileReader();

    resetExportState();
    resetSplitPreviewState();

    if (!file) {
        currentScreenshotB64 = '';
        currentScreenshotDataUrl = '';
        updateSingleExportButtonState();
        $('.screenshot-preview').empty();
        return;
    }

    reader.onloadend = function () {
        var result = String(reader.result || '');

        currentScreenshotB64 = result.replace(/^data:.+;base64,/, '');
        currentScreenshotDataUrl = result;
        updateSingleExportButtonState();
        $('.screenshot-preview').html('<div class="text-muted">Preparing split preview...</div>');
        requestSplitPreview();
    };

    reader.onerror = function () {
        currentScreenshotB64 = '';
        currentScreenshotDataUrl = '';
        resetSplitPreviewState();
        updateSingleExportButtonState();
        $('.screenshot-preview').empty();
        showError('Unable to read the selected screenshot.');
    };

    reader.readAsDataURL(file);
}

function handleBatchScreenshotSelection() {
    resetBatchExportState();
    updateBatchSelectionSummary();
    updateBatchExportButtonState();
}

function updateSingleExportButtonState() {
    $('#export-btn').attr('disabled', !currentScreenshotB64 || !isManualGridValid() || isBatchRunning);
}

function updateBatchExportButtonState() {
    $('#batch-export-btn').attr('disabled', !getSelectedBatchFiles().length || isBatchRunning);
}

function requestSplitPreview() {
    if (!currentScreenshotB64) {
        return;
    }

    load();
    post('/split-preview', { image: currentScreenshotB64 })
        .then(function (response) {
            const verticalLines = normalizeLineList(response.verticalLines, response.imageWidth);
            const horizontalLines = normalizeLineList(response.horizontalLines, response.imageHeight);

            splitPreviewState = {
                imageWidth: response.imageWidth,
                imageHeight: response.imageHeight,
                detectedVerticalLines: verticalLines.slice(),
                detectedHorizontalLines: horizontalLines.slice(),
                verticalLines,
                horizontalLines,
                warnings: Array.isArray(response.warnings) ? response.warnings.slice() : []
            };
            selectedGridLine = null;
            renderSplitPreview();
        })
        .catch(function (xhr) {
            const response = xhr.responseJSON || {};
            showError(response.message || 'Split preview failed.');
            splitPreviewState = null;
            renderSplitPreview();
        })
        .finally(function () {
            updateSingleExportButtonState();
            updateGridControlsState();
            endLoad();
        });
}

function normalizeLineList(lines, maxValue) {
    if (!Array.isArray(lines)) {
        return [];
    }

    const unique = new Set();
    lines.forEach(function (line) {
        const value = Number(line);
        if (!Number.isFinite(value)) {
            return;
        }

        unique.add(Math.min(Math.max(Math.round(value), 0), maxValue));
    });

    return Array.from(unique).sort((a, b) => a - b);
}

function renderSplitPreview() {
    if (!currentScreenshotDataUrl) {
        $('.screenshot-preview').empty();
        $('.split-preview-status').html('');
        updateGridControlsState();
        return;
    }

    if (!splitPreviewState) {
        $('.screenshot-preview').html(
            `<img class="img-fluid"
                alt="Character box screenshot"
                src="${currentScreenshotDataUrl}"
                style="margin: auto; display: block"
                width=200>`
        );
        $('.split-preview-status').html('');
        updateGridControlsState();
        return;
    }

    const verticalLines = splitPreviewState.verticalLines;
    const horizontalLines = splitPreviewState.horizontalLines;
    const verticalMarkup = verticalLines.map(function (line, index) {
        const isSelected = selectedGridLine &&
            selectedGridLine.axis === 'vertical' &&
            selectedGridLine.index === index;
        return `<div class="split-preview__line split-preview__line--vertical ${isSelected ? 'split-preview__line--selected' : ''}"
            data-axis="vertical"
            data-index="${index}"
            style="left: ${(line / splitPreviewState.imageWidth) * 100}%"></div>`;
    }).join('');
    const horizontalMarkup = horizontalLines.map(function (line, index) {
        const isSelected = selectedGridLine &&
            selectedGridLine.axis === 'horizontal' &&
            selectedGridLine.index === index;
        return `<div class="split-preview__line split-preview__line--horizontal ${isSelected ? 'split-preview__line--selected' : ''}"
            data-axis="horizontal"
            data-index="${index}"
            style="top: ${(line / splitPreviewState.imageHeight) * 100}%"></div>`;
    }).join('');

    $('.screenshot-preview').html(
        `<div class="split-preview__stage">
            <img class="img-fluid"
                alt="Character box screenshot split preview"
                src="${currentScreenshotDataUrl}">
            <div class="split-preview__overlay">${verticalMarkup}${horizontalMarkup}</div>
        </div>`
    );

    $('.split-preview__line').on('mousedown touchstart', startGridDrag);
    renderSplitPreviewStatus();
    updateGridControlsState();
}

function renderSplitPreviewStatus() {
    if (!splitPreviewState) {
        $('.split-preview-status').html('');
        return;
    }

    const slotCount = getManualGridSlotCount();
    const warningMarkup = splitPreviewState.warnings.length
        ? `<div class="alert alert-warning mt-2 mb-0">${splitPreviewState.warnings.map(escapeHtml).join('<br>')}</div>`
        : '';
    const validClass = isManualGridValid() ? 'alert-info' : 'alert-warning';
    const statusMessage = isManualGridValid()
        ? `Preview grid has <strong>${slotCount}</strong> slot${slotCount === 1 ? '' : 's'}.`
        : 'Create at least two vertical and two horizontal lines before analyzing.';

    $('.split-preview-status').html(
        `<div class="alert ${validClass} mb-0">${statusMessage}</div>${warningMarkup}`
    );
}

function addGridLine(axis) {
    if (!splitPreviewState) {
        return;
    }

    const key = axis === 'vertical' ? 'verticalLines' : 'horizontalLines';
    const maxValue = axis === 'vertical' ? splitPreviewState.imageWidth : splitPreviewState.imageHeight;
    const lines = splitPreviewState[key].slice();

    if (lines.length < 2) {
        splitPreviewState[key] = [0, maxValue];
        selectedGridLine = { axis, index: 1 };
        renderSplitPreview();
        updateSingleExportButtonState();
        return;
    }

    let insertAfter = 0;
    let largestGap = -1;
    for (let index = 0; index < lines.length - 1; index += 1) {
        const gap = lines[index + 1] - lines[index];
        if (gap > largestGap) {
            largestGap = gap;
            insertAfter = index;
        }
    }

    const newValue = Math.round(lines[insertAfter] + largestGap / 2);
    lines.splice(insertAfter + 1, 0, newValue);
    splitPreviewState[key] = normalizeLineList(lines, maxValue);
    selectedGridLine = { axis, index: splitPreviewState[key].indexOf(newValue) };
    renderSplitPreview();
    updateSingleExportButtonState();
}

function removeSelectedGridLine() {
    if (!splitPreviewState || !selectedGridLine) {
        return;
    }

    const key = selectedGridLine.axis === 'vertical' ? 'verticalLines' : 'horizontalLines';
    splitPreviewState[key].splice(selectedGridLine.index, 1);
    selectedGridLine = null;
    renderSplitPreview();
    updateSingleExportButtonState();
}

function resetGridLinesToDetected() {
    if (!splitPreviewState) {
        return;
    }

    splitPreviewState.verticalLines = splitPreviewState.detectedVerticalLines.slice();
    splitPreviewState.horizontalLines = splitPreviewState.detectedHorizontalLines.slice();
    selectedGridLine = null;
    renderSplitPreview();
    updateSingleExportButtonState();
}

function startGridDrag(event) {
    if (!splitPreviewState) {
        return;
    }

    event.preventDefault();
    const target = $(event.currentTarget);
    activeGridDrag = {
        axis: target.attr('data-axis'),
        index: Number(target.attr('data-index'))
    };
    selectedGridLine = {
        axis: activeGridDrag.axis,
        index: activeGridDrag.index
    };
    renderSplitPreview();
}

function handleGridDragMove(event) {
    if (!activeGridDrag || !splitPreviewState) {
        return;
    }

    event.preventDefault();
    const value = getGridPointerValue(event, activeGridDrag.axis);
    if (value === null) {
        return;
    }

    const key = activeGridDrag.axis === 'vertical' ? 'verticalLines' : 'horizontalLines';
    const maxValue = activeGridDrag.axis === 'vertical'
        ? splitPreviewState.imageWidth
        : splitPreviewState.imageHeight;
    const clampedValue = Math.min(Math.max(Math.round(value), 0), maxValue);

    splitPreviewState[key][activeGridDrag.index] = clampedValue;
    splitPreviewState[key] = normalizeLineList(splitPreviewState[key], maxValue);
    selectedGridLine = {
        axis: activeGridDrag.axis,
        index: findNearestLineIndex(splitPreviewState[key], clampedValue)
    };
    activeGridDrag.index = selectedGridLine.index;
    renderSplitPreview();
    updateSingleExportButtonState();
}

function endGridDrag() {
    activeGridDrag = null;
}

function getGridPointerValue(event, axis) {
    const image = $('.split-preview__stage img').get(0);
    if (!image || !splitPreviewState) {
        return null;
    }

    const originalEvent = event.originalEvent || event;
    const pointer = originalEvent.touches && originalEvent.touches.length
        ? originalEvent.touches[0]
        : originalEvent;
    const bounds = image.getBoundingClientRect();

    if (!bounds.width || !bounds.height) {
        return null;
    }

    if (axis === 'vertical') {
        return ((pointer.clientX - bounds.left) / bounds.width) * splitPreviewState.imageWidth;
    }

    return ((pointer.clientY - bounds.top) / bounds.height) * splitPreviewState.imageHeight;
}

function findNearestLineIndex(lines, value) {
    if (!lines.length) {
        return 0;
    }

    let nearestIndex = 0;
    let nearestDistance = Math.abs(lines[0] - value);
    for (let index = 1; index < lines.length; index += 1) {
        const distance = Math.abs(lines[index] - value);
        if (distance < nearestDistance) {
            nearestIndex = index;
            nearestDistance = distance;
        }
    }
    return nearestIndex;
}

function getManualGridSlotCount() {
    if (!splitPreviewState) {
        return 0;
    }

    return Math.max(splitPreviewState.verticalLines.length - 1, 0) *
        Math.max(splitPreviewState.horizontalLines.length - 1, 0);
}

function isManualGridValid() {
    return Boolean(
        splitPreviewState &&
        splitPreviewState.verticalLines.length >= 2 &&
        splitPreviewState.horizontalLines.length >= 2 &&
        getManualGridSlotCount() > 0
    );
}

function getManualGridPayload() {
    return {
        verticalLines: splitPreviewState.verticalLines.slice(),
        horizontalLines: splitPreviewState.horizontalLines.slice()
    };
}

function updateGridControlsState() {
    const hasPreview = Boolean(splitPreviewState);
    $('#add-vertical-line-btn, #add-horizontal-line-btn, #reset-grid-lines-btn')
        .attr('disabled', !hasPreview || isBatchRunning);
    $('#remove-grid-line-btn')
        .attr('disabled', !hasPreview || !selectedGridLine || isBatchRunning);
}

function resetSplitPreviewState() {
    splitPreviewState = null;
    selectedGridLine = null;
    activeGridDrag = null;
    $('.split-preview-status').html('');
    updateGridControlsState();
}

function setBatchRunningState(isRunning) {
    isBatchRunning = isRunning;

    $('#screenshot-file').prop('disabled', isRunning);
    $('#batch-screenshot-files').prop('disabled', isRunning);
    $('#expected-count-input, #characters-per-row-input').prop('disabled', isRunning);
    $('.type-filter-input, .class-filter-input').prop('disabled', isRunning);
    $('#batch-auto-download-input').prop('disabled', isRunning);

    updateImageSizeControls();
    updateSingleExportButtonState();
    updateBatchExportButtonState();
    updateGridControlsState();
}

function getSelectedBatchFiles() {
    const input = $('#batch-screenshot-files').get(0);
    return input && input.files ? Array.from(input.files) : [];
}

function updateBatchSelectionSummary() {
    const files = getSelectedBatchFiles();
    const selectedCount = files.length;

    if (!selectedCount) {
        $('.batch-selection-summary').html(
            '<div class="text-muted">No batch screenshots selected yet.</div>'
        );
        return;
    }

    const visibleNames = files
        .slice(0, 3)
        .map(file => `<code>${escapeHtml(file.name)}</code>`)
        .join(', ');
    const remainingCount = selectedCount - Math.min(selectedCount, 3);
    const remainder = remainingCount > 0 ? ` and <strong>${remainingCount}</strong> more` : '';
    const fileLabel = selectedCount === 1 ? 'screenshot' : 'screenshots';
    const suffix = visibleNames ? ` Selected: ${visibleNames}${remainder}.` : '';

    $('.batch-selection-summary').html(
        `<div class="text-muted"><strong>${selectedCount}</strong> ${fileLabel} ready for batch export.${suffix}</div>`
    );
}

function exportCharacterBox() {
    const exportOptionsResult = getValidatedExportOptions(true);

    clearMessages();
    disableToggleNames();
    disableDownloadExport();

    if (!currentScreenshotB64) {
        showError('Pick a screenshot before exporting.');
        return;
    }

    if (!isManualGridValid()) {
        showError('Review the split preview and create at least one valid slot before analyzing.');
        return;
    }

    if (!exportOptionsResult.valid) {
        showError(exportOptionsResult.message);
        return;
    }

    exportOptionsResult.options.manualGrid = getManualGridPayload();

    load();
    post('/export', buildExportRequestBody(currentScreenshotB64, exportOptionsResult.options))
        .then(renderExport)
        .catch(renderExportError)
        .finally(endLoad);
}

async function exportBatchCharacterBoxes() {
    const files = getSelectedBatchFiles();
    const exportOptionsResult = getValidatedExportOptions(false);
    const autoDownload = $('#batch-auto-download-input').is(':checked');

    clearBatchError();

    if (!files.length) {
        showBatchError('Pick at least one screenshot before starting batch export.');
        return;
    }

    if (!exportOptionsResult.valid) {
        showBatchError(exportOptionsResult.message);
        return;
    }

    resetBatchExportState();
    batchExportState = createBatchExportState(files.length);
    renderBatchProgress();
    setBatchRunningState(true);

    try {
        for (let index = 0; index < files.length; index += 1) {
            const file = files[index];

            batchExportState.currentFileName = file.name;
            batchExportState.currentIndex = index + 1;
            renderBatchProgress();

            try {
                const imageB64 = await readFileAsBase64(file);
                const response = await post(
                    '/export',
                    buildExportRequestBody(imageB64, exportOptionsResult.options)
                );

                mergeBatchFavorites(batchExportState, response.characters || []);
                batchExportState.succeeded += 1;
            } catch (error) {
                batchExportState.failed += 1;
                batchExportState.failures.push({
                    fileName: file.name,
                    message: extractBatchFailureMessage(error)
                });
            }

            batchExportState.processed += 1;
            renderBatchProgress();
        }

        finalizeBatchExport(autoDownload);
    } finally {
        setBatchRunningState(false);
        renderBatchProgress();
        updateBatchSelectionSummary();
    }
}

function getValidatedExportOptions(returnThumbnails) {
    const imageSizeConfig = getActiveImageSizeConfig();
    const expectedCountConfig = getExpectedCountConfig();
    const charactersPerRowConfig = getCharactersPerRowConfig();
    const types = getSelectedTypes();
    const classes = getSelectedClasses();
    const options = {
        returnThumbnails,
        types,
        classes
    };

    if (!imageSizeConfig.valid) {
        return imageSizeConfig;
    }

    if (!expectedCountConfig.valid) {
        return expectedCountConfig;
    }

    if (!charactersPerRowConfig.valid) {
        return charactersPerRowConfig;
    }

    if (imageSizeConfig.mode === 'custom') {
        options.imageWidth = imageSizeConfig.width;
        options.imageHeight = imageSizeConfig.height;
    } else {
        options.imageSize = imageSizeConfig.size;
    }

    if (expectedCountConfig.hasValue) {
        options.expectedCount = expectedCountConfig.value;
    }

    if (charactersPerRowConfig.hasValue) {
        options.charactersPerRow = charactersPerRowConfig.value;
    }

    return {
        valid: true,
        options
    };
}

function buildExportRequestBody(image, exportOptions) {
    return Object.assign({ image }, exportOptions);
}

function updateImageSizeControls() {
    const mode = getImageSizeMode();
    const squareMode = mode === 'square';

    $('#image-size-square-controls').toggleClass('d-none', !squareMode);
    $('#image-size-custom-controls').toggleClass('d-none', squareMode);
    $('input[name="image-size-mode"]').prop('disabled', isBatchRunning);
    $('#image-size-slider').prop('disabled', !squareMode || isBatchRunning);
    $('#image-width-input, #image-height-input').prop('disabled', squareMode || isBatchRunning);

    updateImageSizeDisplay();
}

function updateImageSizeDisplay() {
    const imageSizeConfig = getActiveImageSizeConfig();

    if (!imageSizeConfig.valid) {
        $('.slider-disp').text(`Image Size: invalid (${IMAGE_SIZE_MIN}-${IMAGE_SIZE_MAX})`);
        return;
    }

    const modeLabel = imageSizeConfig.mode === 'custom' ? 'custom' : 'square';
    $('.slider-disp').text(`Image Size: ${imageSizeConfig.width}x${imageSizeConfig.height} (${modeLabel})`);
}

function getImageSizeMode() {
    const mode = $('input[name="image-size-mode"]:checked').val();
    return mode === 'custom' ? 'custom' : 'square';
}

function parseImageSizeValue(rawValue, fieldName) {
    const value = Number(rawValue);

    if (!Number.isInteger(value)) {
        return {
            valid: false,
            message: `${fieldName} must be an integer between ${IMAGE_SIZE_MIN} and ${IMAGE_SIZE_MAX}.`
        };
    }

    if (value < IMAGE_SIZE_MIN || value > IMAGE_SIZE_MAX) {
        return {
            valid: false,
            message: `${fieldName} must be between ${IMAGE_SIZE_MIN} and ${IMAGE_SIZE_MAX}.`
        };
    }

    return {
        valid: true,
        value
    };
}

function getActiveImageSizeConfig() {
    const mode = getImageSizeMode();

    if (mode === 'custom') {
        const parsedWidth = parseImageSizeValue($('#image-width-input').val(), 'Image width');
        if (!parsedWidth.valid) {
            return parsedWidth;
        }

        const parsedHeight = parseImageSizeValue($('#image-height-input').val(), 'Image height');
        if (!parsedHeight.valid) {
            return parsedHeight;
        }

        return {
            valid: true,
            mode,
            width: parsedWidth.value,
            height: parsedHeight.value,
            size: parsedWidth.value
        };
    }

    const parsedSize = parseImageSizeValue($('#image-size-slider').val(), 'Image size');
    if (!parsedSize.valid) {
        return parsedSize;
    }

    return {
        valid: true,
        mode,
        width: parsedSize.value,
        height: parsedSize.value,
        size: parsedSize.value
    };
}

function parseOptionalPositiveIntegerValue(rawValue, fieldName) {
    if (rawValue === null || rawValue === undefined) {
        return { valid: true, hasValue: false };
    }

    if (typeof rawValue === 'string' && rawValue.trim() === '') {
        return { valid: true, hasValue: false };
    }

    if (typeof rawValue === 'boolean') {
        return {
            valid: false,
            hasValue: true,
            message: `${fieldName} must be a positive integer.`
        };
    }

    const value = Number(rawValue);
    if (!Number.isInteger(value) || value <= 0) {
        return {
            valid: false,
            hasValue: true,
            message: `${fieldName} must be a positive integer.`
        };
    }

    return {
        valid: true,
        hasValue: true,
        value
    };
}

function getExpectedCountConfig() {
    return parseOptionalPositiveIntegerValue($('#expected-count-input').val(), 'Expected characters');
}

function getCharactersPerRowConfig() {
    return parseOptionalPositiveIntegerValue($('#characters-per-row-input').val(), 'Characters per row');
}

function renderExport(response) {
    const characters = response.characters || [];
    const thumbnails = response.thumbnails || [];
    const appliedTypes = Array.isArray(response.appliedTypes) ? response.appliedTypes : [];
    const appliedClasses = Array.isArray(response.appliedClasses) ? response.appliedClasses : [];
    const recognizedCount = characters.length;
    const expectedCount = Number.isInteger(response.expectedCount) ? response.expectedCount : null;
    const detectedCount = Number.isInteger(response.detectedCount) ? response.detectedCount : recognizedCount;
    const countMatch = typeof response.countMatch === 'boolean'
        ? response.countMatch
        : (expectedCount === null || expectedCount === detectedCount);
    const countWarning = typeof response.countWarning === 'string' ? response.countWarning : '';
    const charactersPerRow = Number.isInteger(response.charactersPerRow) ? response.charactersPerRow : null;
    const rowCountMatch = typeof response.rowCountMatch === 'boolean'
        ? response.rowCountMatch
        : (charactersPerRow === null || detectedCount % charactersPerRow === 0);
    const rowCountWarning = typeof response.rowCountWarning === 'string' ? response.rowCountWarning : '';
    const favoritesPayload = buildFavoritesExportPayload(characters);
    const uniqueCount = favoritesPayload.characters.length;
    const countWarningSummary = buildCountWarningSummary(
        expectedCount,
        detectedCount,
        countMatch,
        countWarning
    );
    const rowWarningSummary = buildRowWarningSummary(
        charactersPerRow,
        detectedCount,
        rowCountMatch,
        rowCountWarning
    );

    lastExportResponse = response;

    $('.export-summary').html(
        `<div class="alert alert-success">
            Recognized ${recognizedCount} OPTC unit${recognizedCount === 1 ? '' : 's'}.
            <strong>${uniqueCount}</strong> unique favorite-ready id${uniqueCount === 1 ? '' : 's'}.
            <div class="mt-2">${buildFilterSummary(appliedTypes, appliedClasses)}</div>
        </div>${countWarningSummary}${rowWarningSummary}`
    );

    if (!recognizedCount) {
        disableToggleNames();
        $('.export-disp').html('');
        disableDownloadExport();
        return;
    }

    setNamesVisibility(true);
    enableToggleNames();
    enableDownloadExport();
    $('.export-disp').html(renderResultGrid(characters, thumbnails));
}

function buildCountWarningSummary(expectedCount, detectedCount, countMatch, countWarning) {
    if (expectedCount === null || countMatch) {
        return '';
    }

    const fallbackWarning = (
        `Detected ${detectedCount} characters, but expected ${expectedCount}. ` +
        'Review the screenshot and filters, then retry if needed.'
    );
    const message = countWarning || fallbackWarning;
    return `<div class="alert alert-warning mt-3 mb-0">${escapeHtml(message)}</div>`;
}

function buildRowWarningSummary(charactersPerRow, detectedCount, rowCountMatch, rowCountWarning) {
    if (charactersPerRow === null || rowCountMatch) {
        return '';
    }

    const fallbackWarning = (
        `Detected ${detectedCount} characters, which is not divisible by charactersPerRow=${charactersPerRow}. ` +
        'Review the value and retry if needed.'
    );
    const message = rowCountWarning || fallbackWarning;
    return `<div class="alert alert-warning mt-3 mb-0">${escapeHtml(message)}</div>`;
}

function renderResultGrid(characters, thumbnails) {
    const cards = characters.map((character, index) => {
        const thumbnail = thumbnails[index] || '';
        const url = 'https://optc-db.github.io/characters/#/view/' + character.number;
        const position = String(index + 1).padStart(2, '0');
        const escapedName = escapeHtml(character.name);

        return `
            <article class="result-card">
                <div class="result-card__thumb">
                    ${thumbnail ? `<img src="${thumbnail}" alt="Detected crop for ${escapedName}">` : '<div class="result-card__thumb-empty">No crop</div>'}
                </div>
                <div class="result-card__meta">
                    <div class="result-card__position">Slot ${position}</div>
                    <div class="result-card__id">#${character.number}</div>
                    <a class="result-card__name" href="${url}" target="_blank" rel="noopener noreferrer">${escapedName}</a>
                </div>
            </article>
        `;
    });

    return `<section class="export-grid">${cards.join('')}</section>`;
}

function downloadFavoritesExport() {
    if (!lastExportResponse || !Array.isArray(lastExportResponse.characters) || !lastExportResponse.characters.length) {
        return;
    }

    const payload = buildFavoritesExportPayload(lastExportResponse.characters);
    downloadFavoritesPayload(payload, `optcbx-favorites-${buildTimestamp()}.json`);
}

function downloadBatchFavoritesExport() {
    if (!lastBatchFavoritesPayload || !Array.isArray(lastBatchFavoritesPayload.characters) || !lastBatchFavoritesPayload.characters.length) {
        return;
    }

    downloadFavoritesPayload(
        lastBatchFavoritesPayload,
        `optcbx-favorites-batch-${buildTimestamp()}.json`
    );
}

function downloadFavoritesPayload(payload, filename) {
    const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');

    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
}

function buildFavoritesExportPayload(characters) {
    const seen = new Set();
    const compactCharacters = [];

    characters.forEach(character => {
        const normalizedNumber = Number(character.number);

        if (!Number.isInteger(normalizedNumber) || normalizedNumber <= 0 || seen.has(normalizedNumber)) {
            return;
        }

        seen.add(normalizedNumber);
        compactCharacters.push({
            number: normalizedNumber,
            name: String(character.name || '').trim()
        });
    });

    return {
        characters: compactCharacters
    };
}

function buildTimestamp() {
    const now = new Date();
    const pad = value => String(value).padStart(2, '0');
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function load() {
    $('.loading-wrapper').show();

    $('html, body').css({
        overflow: 'hidden',
        height: '100%'
    });
}

function endLoad() {
    $('.loading-wrapper').hide();

    $('html, body').css({
        overflow: 'auto',
        height: 'auto'
    });
}

function renderExportError(xhr) {
    const response = xhr.responseJSON || {};
    const appliedTypes = Array.isArray(response.appliedTypes)
        ? response.appliedTypes
        : getSelectedTypes();
    const appliedClasses = Array.isArray(response.appliedClasses)
        ? response.appliedClasses
        : getSelectedClasses();
    let message = response.message || 'Export failed.';

    if (response.runtime && response.runtime.missing_web_requirements) {
        const missing = response.runtime.missing_web_requirements
            .map(item => `<li><code>${item.path}</code> - ${item.help}</li>`)
            .join('');

        if (missing) {
            message += `<ul class="mb-0 mt-2">${missing}</ul>`;
        }
    }

    message += buildFilterDetails(appliedTypes, appliedClasses);

    showError(message);
    lastExportResponse = null;
    disableToggleNames();
    disableDownloadExport();
    $('.export-disp').html('');
    $('.export-summary').html('');
}

function createBatchExportState(totalFiles) {
    return {
        totalFiles,
        processed: 0,
        succeeded: 0,
        failed: 0,
        currentIndex: 0,
        currentFileName: '',
        uniqueFavorites: 0,
        failures: [],
        favoritesPayload: {
            characters: []
        },
        favoritesSeen: new Set()
    };
}

function mergeBatchFavorites(state, characters) {
    const payload = buildFavoritesExportPayload(characters);

    payload.characters.forEach(character => {
        if (state.favoritesSeen.has(character.number)) {
            return;
        }

        state.favoritesSeen.add(character.number);
        state.favoritesPayload.characters.push(character);
    });

    state.uniqueFavorites = state.favoritesPayload.characters.length;
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();

        reader.onloadend = function () {
            const result = String(reader.result || '');

            if (!result) {
                reject(new Error(`Unable to read ${file.name}.`));
                return;
            }

            resolve(result.replace(/^data:.+;base64,/, ''));
        };

        reader.onerror = function () {
            reject(new Error(`Unable to read ${file.name}.`));
        };

        reader.readAsDataURL(file);
    });
}

function extractBatchFailureMessage(error) {
    if (error && error.responseJSON && error.responseJSON.message) {
        return String(error.responseJSON.message);
    }

    if (error instanceof Error && error.message) {
        return error.message;
    }

    if (typeof error === 'string' && error.trim()) {
        return error;
    }

    return 'Batch export failed for this screenshot.';
}

function finalizeBatchExport(autoDownload) {
    const state = batchExportState;
    const hasFavorites = state.favoritesPayload.characters.length > 0;
    const summaryClass = state.failed === 0
        ? 'alert-success'
        : (state.succeeded > 0 ? 'alert-warning' : 'alert-danger');
    const jsonSummary = hasFavorites
        ? `Merged <strong>${state.uniqueFavorites}</strong> unique favorite-ready id${state.uniqueFavorites === 1 ? '' : 's'} into one JSON file.`
        : 'No favorites JSON was produced because every screenshot failed.';
    const autoDownloadSummary = autoDownload && hasFavorites
        ? '<div class="mt-2">Auto-download started using the browser default download location.</div>'
        : '';

    lastBatchFavoritesPayload = hasFavorites
        ? { characters: state.favoritesPayload.characters.slice() }
        : null;

    if (lastBatchFavoritesPayload) {
        enableBatchDownload();
    } else {
        disableBatchDownload();
    }

    $('.batch-export-summary').html(
        `<div class="alert ${summaryClass}">
            Processed <strong>${state.totalFiles}</strong> screenshot${state.totalFiles === 1 ? '' : 's'}.
            Succeeded: <strong>${state.succeeded}</strong>.
            Failed: <strong>${state.failed}</strong>.
            <div class="mt-2">${jsonSummary}</div>
            ${autoDownloadSummary}
        </div>`
    );

    renderBatchFailures(state.failures);
    renderBatchProgress();

    if (autoDownload && lastBatchFavoritesPayload) {
        downloadBatchFavoritesExport();
    }
}

function renderBatchProgress() {
    if (!batchExportState) {
        $('.batch-export-status').addClass('d-none').html('');
        return;
    }

    const state = batchExportState;
    const percent = state.totalFiles === 0
        ? 0
        : Math.round((state.processed / state.totalFiles) * 100);
    const currentLabel = isBatchRunning && state.currentFileName
        ? `Processing <strong>${escapeHtml(state.currentFileName)}</strong> (${state.currentIndex}/${state.totalFiles})`
        : 'Batch complete.';

    $('.batch-export-status').removeClass('d-none').html(
        `<div class="batch-export__status-card">
            <div class="batch-export__status-header">
                <strong>Batch progress</strong>
                <span>${percent}%</span>
            </div>
            <div class="progress batch-export__progress">
                <div class="progress-bar" role="progressbar"
                    style="width: ${percent}%"
                    aria-valuenow="${percent}"
                    aria-valuemin="0"
                    aria-valuemax="100">${percent}%</div>
            </div>
            <div class="batch-export__stats">
                <span>Total: <strong>${state.totalFiles}</strong></span>
                <span>Processed: <strong>${state.processed}</strong></span>
                <span>Succeeded: <strong>${state.succeeded}</strong></span>
                <span>Failed: <strong>${state.failed}</strong></span>
                <span>Unique favorites: <strong>${state.uniqueFavorites}</strong></span>
            </div>
            <div class="batch-export__current">${currentLabel}</div>
        </div>`
    );
}

function renderBatchFailures(failures) {
    if (!failures.length) {
        $('.batch-export-failures').html('');
        return;
    }

    const visibleFailures = failures.slice(0, MAX_RENDERED_BATCH_FAILURES);
    const remainingFailures = failures.length - visibleFailures.length;
    const items = visibleFailures.map(failure => (
        `<li><span class="batch-export__failure-name">${escapeHtml(failure.fileName)}</span>: ${escapeHtml(failure.message)}</li>`
    )).join('');
    const overflowNote = remainingFailures > 0
        ? `<div class="mt-2 text-muted">Showing the first ${visibleFailures.length} failures. ${remainingFailures} more were omitted from the on-page list.</div>`
        : '';

    $('.batch-export-failures').html(
        `<div class="alert alert-warning mb-0">
            <strong>Failed screenshots (${failures.length})</strong>
            <ul class="batch-export__failure-list mt-2 mb-0">${items}</ul>
            ${overflowNote}
        </div>`
    );
}

function resetExportState() {
    lastExportResponse = null;
    disableToggleNames();
    disableDownloadExport();
    clearMessages();
    $('.export-disp').html('');
}

function resetBatchExportState() {
    batchExportState = null;
    lastBatchFavoritesPayload = null;
    disableBatchDownload();
    clearBatchError();
    $('.batch-export-summary').html('');
    $('.batch-export-failures').html('');
    renderBatchProgress();
}

function clearMessages() {
    $('.export-error').html('');
    $('.export-summary').html('');
}

function clearBatchError() {
    $('.batch-export-error').html('');
}

function showError(message) {
    $('.export-error').html(`<div class="alert alert-danger">${message}</div>`);
}

function showBatchError(message) {
    $('.batch-export-error').html(
        `<div class="alert alert-danger">${escapeHtml(message)}</div>`
    );
}

function disableDownloadExport() {
    $('#download-export-btn').attr('disabled', true);
}

function enableDownloadExport() {
    $('#download-export-btn').attr('disabled', false);
}

function disableBatchDownload() {
    $('#download-batch-export-btn').attr('disabled', true);
}

function enableBatchDownload() {
    $('#download-batch-export-btn').attr('disabled', false);
}

function toggleResultNames() {
    if ($('#toggle-names-btn').is(':disabled')) {
        return;
    }

    setNamesVisibility(!areNamesVisible);
}

function setNamesVisibility(isVisible) {
    areNamesVisible = isVisible;
    $('.export-disp').toggleClass('export-disp--hide-names', !areNamesVisible);
    updateToggleNamesButton();
}

function updateToggleNamesButton() {
    $('#toggle-names-btn').text(areNamesVisible ? 'Hide names' : 'Show names');
}

function disableToggleNames() {
    setNamesVisibility(true);
    $('#toggle-names-btn').attr('disabled', true);
}

function enableToggleNames() {
    $('#toggle-names-btn').attr('disabled', false);
    updateToggleNamesButton();
}

function getSelectedTypes() {
    return $('.type-filter-input:checked')
        .map(function () {
            return String(this.value || '').trim().toUpperCase();
        })
        .get()
        .filter(Boolean);
}

function getSelectedClasses() {
    return $('.class-filter-input:checked')
        .map(function () {
            return String(this.value || '').trim();
        })
        .get()
        .filter(Boolean);
}

function buildFilterSummary(appliedTypes, appliedClasses) {
    return (
        `<div><strong>Type filter:</strong> ${formatFilterValue(appliedTypes, 'all types')}</div>` +
        `<div><strong>Class filter:</strong> ${formatFilterValue(appliedClasses, 'all classes')}</div>`
    );
}

function buildFilterDetails(appliedTypes, appliedClasses) {
    return `<div class="mt-2">${buildFilterSummary(appliedTypes, appliedClasses)}</div>`;
}

function formatFilterValue(values, emptyLabel) {
    if (!Array.isArray(values) || !values.length) {
        return emptyLabel;
    }

    return `<strong>${escapeHtml(values.join(', '))}</strong>`;
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function post(path, body) {
    return new Promise((resolve, reject) => {
        $.ajax({
            url: path,
            type: 'POST',
            data: JSON.stringify(body),
            contentType: 'application/json',
            dataType: 'json',
            success: data => resolve(data),
            error: xhr => reject(xhr)
        });
    });
}
