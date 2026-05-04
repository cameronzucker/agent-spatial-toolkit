// app.js — wizard state machine and Phase 2a wiring (Task 1.D.3).
//
// Loaded by index.html via <script src="/ui/app.js" defer>. Uses the
// helpers shipped in 1.D.2 (window.spatialUI.{captureClick,
// loadImageToCanvas, getEXIFFromUploaded, attachDragDrop}) to render
// the wizard's first interactive phase.
//
// Phase 2a — Upload + validate (per spec §3 lines 153–160)
//   - Surface every photo from /api/state as a thumbnail card.
//   - Each card carries: image, EXIF preview, lens dropdown, view-label
//     dropdown.
//   - Drag-drop zone for additional photos (collected via
//     spatialUI.attachDragDrop; backend upload route is a future task).
//   - "Next" button advances the phase machine to 2b (1.D.4 fills it).
//
// Client-side state lives on window.spatialState and is read by
// subsequent phases. No backend round-trip for lens/view-label choice
// at this phase; 1.D.5's /api/anchors POST consumes the lens choice.
//
// XSS posture
// -----------
// All DOM nodes are created via document.createElement and populated
// via .textContent / .className / .src / DOM properties — never via
// innerHTML or insertAdjacentHTML. The single exception that the hook
// would flag (clearing a container's children for re-render) uses the
// removeChild loop, not ``element.innerHTML = ''``.
(function () {
    'use strict';

    // Lens dropdown options. Placeholder until the backend exposes a
    // calibrated lens catalog (issue #4 hygiene territory). The first
    // entry is a sentinel; the rest cover the cameras the spec's
    // CHECKPOINT 1 setup uses (Pi 5 camera + iPhone). The "other" entry
    // signals manual entry mode for a future intrinsics-override flow.
    var LENS_OPTIONS = [
        { value: '', label: '— Select lens —' },
        { value: 'pi_camera_module_3_wide', label: 'Pi Camera Module 3 (wide)' },
        { value: 'pi_camera_module_3_standard', label: 'Pi Camera Module 3 (standard)' },
        { value: 'iphone_15_pro_24mm', label: 'iPhone 15 Pro — 24mm equiv (main)' },
        { value: 'iphone_15_pro_13mm', label: 'iPhone 15 Pro — 13mm equiv (ultrawide)' },
        { value: 'iphone_15_pro_77mm', label: 'iPhone 15 Pro — 77mm equiv (telephoto)' },
        { value: 'other', label: 'Other (manual entry)' },
    ];

    // View-label dropdown options per spec §3 Phase 2a line 157:
    // "top-down", "long-edge-A", "side-iso", "other". Order preserved
    // from the spec text.
    var VIEW_LABEL_OPTIONS = [
        { value: '', label: '— Select view —' },
        { value: 'top-down', label: 'top-down' },
        { value: 'long-edge-A', label: 'long-edge-A' },
        { value: 'side-iso', label: 'side-iso' },
        { value: 'other', label: 'other' },
    ];

    // Initialize global wizard state. Subsequent phases read this.
    window.spatialState = window.spatialState || { photos: {} };

    function init() {
        fetch('/api/state')
            .then(function (r) { return r.json(); })
            .then(function (state) {
                renderThumbnails(state.photos || []);
                wirePhaseControls();
            })
            .catch(function (err) {
                // Surface init failure visibly so a user (or test) sees a
                // clear signal rather than a silently-empty Phase 2a.
                var container = document.getElementById('phase-2a-thumbnails');
                if (container) {
                    container.textContent = 'Failed to load wizard state: ' + err.message;
                }
            });
    }

    function renderThumbnails(photos) {
        var container = document.getElementById('phase-2a-thumbnails');
        if (!container) return;
        clearChildren(container);
        photos.forEach(function (photo) {
            var card = buildThumbnailCard(photo);
            container.appendChild(card);
            // Track per-photo state so phase 2c (1.D.5) can read it.
            window.spatialState.photos[photo.id] = {
                url: photo.url,
                lens: '',
                viewLabel: '',
                exif: null,  // populated by populateExif below
            };
            // Async EXIF parse — does not block thumbnail render.
            populateExif(photo, card);
        });
    }

    // Empty an element via the DOM API rather than `innerHTML = ''`.
    // Equivalent for empty content; avoids the XSS-shaped pattern even
    // though clearing is benign.
    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function buildThumbnailCard(photo) {
        var card = document.createElement('div');
        card.className = 'thumbnail';
        card.dataset.photoId = photo.id;

        var img = document.createElement('img');
        img.src = photo.url;
        img.alt = photo.id;
        card.appendChild(img);

        var meta = document.createElement('div');
        meta.className = 'thumbnail-meta';

        var name = document.createElement('div');
        name.className = 'thumbnail-name';
        name.textContent = photo.id;
        meta.appendChild(name);

        var exifInfo = document.createElement('div');
        exifInfo.className = 'exif-info';
        exifInfo.textContent = 'Reading EXIF…';
        meta.appendChild(exifInfo);

        meta.appendChild(buildLabeledSelect('Lens', 'lens-select', LENS_OPTIONS, function (val) {
            window.spatialState.photos[photo.id].lens = val;
        }));

        meta.appendChild(buildLabeledSelect(
            'View', 'view-label-select', VIEW_LABEL_OPTIONS,
            function (val) { window.spatialState.photos[photo.id].viewLabel = val; }
        ));

        card.appendChild(meta);
        return card;
    }

    function buildLabeledSelect(labelText, selectClass, options, onChange) {
        var wrapper = document.createElement('label');
        wrapper.className = 'select-row';

        var labelSpan = document.createElement('span');
        labelSpan.textContent = labelText + ': ';
        wrapper.appendChild(labelSpan);

        var select = document.createElement('select');
        select.className = selectClass;
        options.forEach(function (opt) {
            var o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            select.appendChild(o);
        });
        select.addEventListener('change', function () { onChange(select.value); });
        wrapper.appendChild(select);

        return wrapper;
    }

    function populateExif(photo, card) {
        var exifInfo = card.querySelector('.exif-info');
        if (!exifInfo) return;
        // Fetch the photo as a Blob, wrap as a File, and pass through the
        // helper. The helper takes a File; the wrapper bridges from the
        // URL-served photo into the same code path drag-dropped photos use.
        fetch(photo.url)
            .then(function (r) { return r.blob(); })
            .then(function (blob) {
                var file = new File([blob], photo.id, { type: blob.type });
                window.spatialUI.getEXIFFromUploaded(file, function (exif) {
                    window.spatialState.photos[photo.id].exif = exif;
                    exifInfo.textContent = formatExif(exif);
                });
            })
            .catch(function () {
                exifInfo.textContent = 'EXIF unavailable (fetch failed)';
            });
    }

    function formatExif(exif) {
        if (!exif) return 'EXIF unavailable (no metadata in file)';
        var parts = [];
        if (exif.make || exif.model) {
            parts.push(((exif.make || '') + ' ' + (exif.model || '')).trim());
        }
        if (exif.lensModel) parts.push('lens: ' + exif.lensModel);
        if (exif.focalLength) parts.push('focal: ' + exif.focalLength + 'mm');
        if (exif.focalLength35mm) parts.push('(' + exif.focalLength35mm + 'mm 35mm-equiv)');
        return parts.length ? parts.join(' · ') : 'EXIF unavailable (no recognized tags)';
    }

    function wirePhaseControls() {
        var nextBtn = document.getElementById('phase-2a-next');
        if (nextBtn) {
            nextBtn.addEventListener('click', function () { advancePhase('2a', '2b'); });
        }
        var dropZone = document.getElementById('phase-2a-drop-zone');
        if (dropZone && window.spatialUI) {
            window.spatialUI.attachDragDrop(dropZone, function (_files) {
                // Backend upload route lands in a future task; for now the
                // drop zone exists so the UI surface is real but a no-op
                // for additional photos. Surface the receipt so users
                // aren't confused by silent drops.
                dropZone.classList.add('drop-pending');
            });
        }
    }

    function advancePhase(fromId, toId) {
        var from = document.getElementById('phase-' + fromId);
        var to = document.getElementById('phase-' + toId);
        if (from) from.hidden = true;
        if (to) to.hidden = false;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
