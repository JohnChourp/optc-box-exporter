var currentScreenshotB64 = '';
var lastExportResponse = null;
var areNamesVisible = true;
const IMAGE_SIZE_MIN = 32;
const IMAGE_SIZE_MAX = 256;

$(document).ready(function () {
    $('#sidebarCollapse').on('click', function () {
        $('#sidebar').toggleClass('active');
    });

    $('#image-size-slider').on('input change', updateImageSizeDisplay);
    $('input[name="image-size-mode"]').on('change', updateImageSizeControls);
    $('#image-width-input, #image-height-input').on('input change', updateImageSizeDisplay);

    $('#screenshot-file').change(function () {
        var file = this.files[0],
            reader = new FileReader();

        resetExportState();

        if (!file) {
            currentScreenshotB64 = '';
            $("#export-btn").attr('disabled', true);
            $('.screenshot-preview').empty();
            return;
        }

        reader.onloadend = function () {
            var b64 = reader.result.replace(/^data:.+;base64,/, '');
            currentScreenshotB64 = b64;
            $("#export-btn").attr('disabled', false);
            $('.screenshot-preview').html(
                `<img class="img-fluid" 
                    alt="Character box screenshot" 
                    src="${reader.result}" 
                    style="margin: auto; display: block" 
                    width=200>`
            );
        };

        reader.readAsDataURL(file);
    });

    updateImageSizeControls();
    $('.loading-wrapper').hide();
});

function exportCharacterBox() {
    const imageSizeConfig = getActiveImageSizeConfig();
    const expectedCountConfig = getExpectedCountConfig();
    const charactersPerRowConfig = getCharactersPerRowConfig();
    const image = currentScreenshotB64;
    const types = getSelectedTypes();
    const classes = getSelectedClasses();
    const body = {
        image,
        returnThumbnails: true,
        types,
        classes
    };

    clearMessages();
    disableToggleNames();
    disableDownloadExport();

    if (!image) {
        showError('Pick a screenshot before exporting.');
        return;
    }

    if (!imageSizeConfig.valid) {
        showError(imageSizeConfig.message);
        return;
    }

    if (!expectedCountConfig.valid) {
        showError(expectedCountConfig.message);
        return;
    }

    if (!charactersPerRowConfig.valid) {
        showError(charactersPerRowConfig.message);
        return;
    }

    if (imageSizeConfig.mode === 'custom') {
        body.imageWidth = imageSizeConfig.width;
        body.imageHeight = imageSizeConfig.height;
    } else {
        body.imageSize = imageSizeConfig.size;
    }

    if (expectedCountConfig.hasValue) {
        body.expectedCount = expectedCountConfig.value;
    }

    if (charactersPerRowConfig.hasValue) {
        body.charactersPerRow = charactersPerRowConfig.value;
    }

    load();
    post('/export', body)
        .then(renderExport)
        .catch(renderExportError)
        .finally(endLoad);
}

function updateImageSizeControls() {
    const mode = getImageSizeMode();
    const squareMode = mode === 'square';

    $('#image-size-square-controls').toggleClass('d-none', !squareMode);
    $('#image-size-custom-controls').toggleClass('d-none', squareMode);
    $('#image-size-slider').prop('disabled', !squareMode);
    $('#image-width-input, #image-height-input').prop('disabled', squareMode);

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
        const url = "https://optc-db.github.io/characters/#/view/" + character.number;
        const position = String(index + 1).padStart(2, '0');
        const escapedName = escapeHtml(character.name);

        return `
            <article class="result-card">
                <div class="result-card__thumb">
                    ${thumbnail ? `<img src="${thumbnail}" alt="Detected crop for ${escapedName}">` : `<div class="result-card__thumb-empty">No crop</div>`}
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
    const filename = `optcbx-favorites-${buildTimestamp()}.json`;
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

function resetExportState() {
    lastExportResponse = null;
    disableToggleNames();
    disableDownloadExport();
    clearMessages();
    $('.export-disp').html('');
}

function clearMessages() {
    $('.export-error').html('');
    $('.export-summary').html('');
}

function showError(message) {
    $('.export-error').html(`<div class="alert alert-danger">${message}</div>`);
}

function disableDownloadExport() {
    $('#download-export-btn').attr('disabled', true);
}

function enableDownloadExport() {
    $('#download-export-btn').attr('disabled', false);
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
            type: "POST",
            data: JSON.stringify(body),
            contentType: "application/json",
            dataType: "json",
            success: data => resolve(data),
            error: xhr => reject(xhr)
        });
    });
}
