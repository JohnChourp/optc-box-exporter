var currentScreenshotB64 = '';
var lastExportResponse = null;

$(document).ready(function () {
    $('#sidebarCollapse').on('click', function () {
        $('#sidebar').toggleClass('active');
    });

    $('#image-size-slider').change(function () {
        $('.slider-disp').html(`Image Size: ${this.value}x${this.value}`);
    });

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

    $('.loading-wrapper').hide();
});

function exportCharacterBox() {
    const imageSize = parseInt($('#image-size-slider').val());
    const image = currentScreenshotB64;
    const types = getSelectedTypes();
    const classes = getSelectedClasses();
    const body = {
        imageSize,
        image,
        returnThumbnails: true,
        types,
        classes
    };

    clearMessages();
    disableDownloadExport();

    if (!image) {
        showError('Pick a screenshot before exporting.');
        return;
    }

    load();
    post('/export', body)
        .then(renderExport)
        .catch(renderExportError)
        .finally(endLoad);
}

function renderExport(response) {
    const characters = response.characters || [];
    const thumbnails = response.thumbnails || [];
    const appliedTypes = Array.isArray(response.appliedTypes) ? response.appliedTypes : [];
    const appliedClasses = Array.isArray(response.appliedClasses) ? response.appliedClasses : [];
    const recognizedCount = characters.length;
    const favoritesPayload = buildFavoritesExportPayload(characters);
    const uniqueCount = favoritesPayload.characters.length;

    lastExportResponse = response;

    $('.export-summary').html(
        `<div class="alert alert-success">
            Recognized ${recognizedCount} OPTC unit${recognizedCount === 1 ? '' : 's'}.
            <strong>${uniqueCount}</strong> unique favorite-ready id${uniqueCount === 1 ? '' : 's'}.
            <div class="mt-2">${buildFilterSummary(appliedTypes, appliedClasses)}</div>
        </div>`
    );

    if (!recognizedCount) {
        $('.export-disp').html('');
        disableDownloadExport();
        return;
    }

    enableDownloadExport();
    $('.export-disp').html(renderResultGrid(characters, thumbnails));
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
    disableDownloadExport();
    $('.export-disp').html('');
    $('.export-summary').html('');
}

function resetExportState() {
    lastExportResponse = null;
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
