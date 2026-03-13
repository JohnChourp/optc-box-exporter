var currentScreenshotB64 = '';

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

        clearMessages();

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
            )
        };

        reader.readAsDataURL(file);
    });

    $('.loading-wrapper').hide();
});

function exportCharacterBox() {
    const imageSize = parseInt($('#image-size-slider').val());
    const image = currentScreenshotB64;
    const body = {
        imageSize,
        image,
        returnThumbnails: true
    };

    clearMessages();

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
    const n = response.characters.length;
    const characters = response.characters;
    const thumbnails = response.thumbnails || [];

    $('.export-summary').html(
        `<div class="alert alert-success">Recognized ${n} OPTC unit${n === 1 ? '' : 's'}.</div>`
    );

    if (!n) {
        $('.export-disp').html('');
        return;
    }

    let table = `
        <table class="table table-hover">
            <thead>
                <tr>
                    <th scope="col">#</th>
                    <th scope="col">Thumbnail</th>
                    <th scope="col">Name</th>
                </tr>
        </thead>
        <tbody>`;
    for (let i = 0; i < n; i++) {
        const c = characters[i];
        const t = thumbnails[i] || '';
        const url = "https://optc-db.github.io/characters/#/view/" + c.number;

        const row = `
            <tr>
                <th scope="row">${c.number}</th>
                <td><img src="${t}" class="img-fluid"></td>
                <td><a href="${url}" target="_blank" rel="noopener noreferrer">${c.name}</a></td>
            </tr>
        `;
        table += row;
    }
    table = table + `</tbody></table>`;
    $(".export-disp").html(table);
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
    let message = response.message || 'Export failed.';

    if (response.runtime && response.runtime.missing_web_requirements) {
        const missing = response.runtime.missing_web_requirements
            .map(item => `<li><code>${item.path}</code> - ${item.help}</li>`)
            .join('');

        if (missing) {
            message += `<ul class="mb-0 mt-2">${missing}</ul>`;
        }
    }

    showError(message);
    $('.export-disp').html('');
    $('.export-summary').html('');
}

function clearMessages() {
    $('.export-error').html('');
    $('.export-summary').html('');
}

function showError(message) {
    $('.export-error').html(`<div class="alert alert-danger">${message}</div>`);
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
