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

    // Phase 2b frame presets. Spec §3 line 163 describes the canonical
    // PCB convention as "bottom-left corner with components facing up;
    // +X along long edge; +Y along short edge toward GPIO header; +Z
    // away from PCB bottom" — pcb_standard implements that with the
    // four corners as named anchors. ``custom`` is a sentinel for a
    // future free-text frame description (spec line 163's "or fill in
    // free-text alternatives" — the hooks are here but the UI for it
    // ships in a follow-up).
    var FRAME_PRESETS = {
        '': { label: '— Select preset —', anchors: null },
        'pcb_standard': {
            label: 'Standard PCB (origin = bottom-left, +X long, +Y short, +Z up)',
            anchors: function (longMm, shortMm) {
                return [
                    { id: 'pcb_corner_origin', xyz: [0, 0, 0] },
                    { id: 'pcb_corner_x_max', xyz: [longMm, 0, 0] },
                    { id: 'pcb_corner_y_max', xyz: [0, shortMm, 0] },
                    { id: 'pcb_corner_xy_max', xyz: [longMm, shortMm, 0] },
                ];
            },
        },
        'custom': {
            label: 'Custom (define your own anchors below)',
            anchors: null,  // sentinel: parsed from the custom-anchors textarea
        },
    };

    // Sane upper bound on declared dimensions. Most physical parts the
    // toolkit targets fit in a 1m³ envelope; 10m gives plenty of slack
    // without admitting absurdly-large values that would overflow other
    // float math downstream. Backend re-validates at /api/anchors.
    var MAX_DIMENSION_MM = 10_000;

    // Initialize global wizard state. Subsequent phases read this.
    window.spatialState = window.spatialState || { photos: {}, frame: null };

    function init() {
        // Wire phase controls FIRST, independent of /api/state. Without this,
        // a failed state fetch would brick the Next button (no listener
        // attached) and trap the user in Phase 2a forever.
        wirePhaseControls();
        fetch('/api/state')
            .then(function (r) {
                if (!r.ok) {
                    throw new Error('GET /api/state returned ' + r.status);
                }
                return r.json();
            })
            .then(function (state) {
                renderThumbnails(state.photos || []);
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
            // Merge with any prior selection so a re-render (e.g., after a
            // drag-drop upload triggers another /api/state fetch) doesn't
            // silently overwrite user choices.
            var existing = window.spatialState.photos[photo.id] || {};
            window.spatialState.photos[photo.id] = {
                url: photo.url,
                lens: existing.lens || '',
                viewLabel: existing.viewLabel || '',
                exif: existing.exif || null,  // populated by populateExif below
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
            .then(function (r) {
                // Reject 4xx/5xx explicitly. Without this, a 404 HTML body
                // would feed into Blob → FileReader → parseExif → null,
                // surfacing as "no EXIF" instead of "fetch failed".
                if (!r.ok) {
                    throw new Error('photo fetch ' + photo.url + ' returned ' + r.status);
                }
                return r.blob();
            })
            .then(function (blob) {
                var file = new File([blob], photo.id, { type: blob.type });
                window.spatialUI.getEXIFFromUploaded(file, function (exif) {
                    window.spatialState.photos[photo.id].exif = exif;
                    exifInfo.textContent = formatExif(exif);
                    maybeAutoSelectLens(photo, card, exif);
                });
            })
            .catch(function () {
                exifInfo.textContent = 'EXIF unavailable (fetch failed)';
            });
    }

    // When EXIF detects a camera/lens, add a pre-selected "Detected: …"
    // option to the lens dropdown. Spec §3 line 156 calls for the dropdown
    // to be "populated from EXIF if detected" — this satisfies that clause
    // without trying to fuzzy-match arbitrary EXIF lensModel strings against
    // the placeholder LENS_OPTIONS list. The user can still override by
    // picking a different option.
    function maybeAutoSelectLens(photo, card, exif) {
        if (!exif || (!exif.make && !exif.model && !exif.lensModel)) return;
        var select = card.querySelector('select.lens-select');
        if (!select) return;
        var detectedValue = 'exif:detected';
        var detectedLabel = 'Detected: ' + formatExif(exif);
        var option = document.createElement('option');
        option.value = detectedValue;
        option.textContent = detectedLabel;
        // Insert just after the placeholder so the auto-detected option is
        // visible at the top of the real choices.
        if (select.firstChild && select.firstChild.nextSibling) {
            select.insertBefore(option, select.firstChild.nextSibling);
        } else {
            select.appendChild(option);
        }
        select.value = detectedValue;
        window.spatialState.photos[photo.id].lens = detectedValue;
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
        wirePhase2a();
        wirePhase2b();
    }

    function wirePhase2a() {
        var nextBtn = document.getElementById('phase-2a-next');
        if (nextBtn) {
            nextBtn.addEventListener('click', function () { advancePhase('2a', '2b'); });
        }
        var dropZone = document.getElementById('phase-2a-drop-zone');
        if (dropZone && window.spatialUI) {
            window.spatialUI.attachDragDrop(dropZone, function (files) {
                // Backend upload route lands in a future task; surface a
                // visible "not yet wired" message rather than silently
                // dropping the files (which would feel like a bug to users).
                dropZone.classList.add('drop-pending');
                var status = dropZone.querySelector('.drop-status') || (function () {
                    var s = document.createElement('p');
                    s.className = 'drop-status';
                    dropZone.appendChild(s);
                    return s;
                })();
                status.textContent = (
                    'Received ' + files.length + ' file(s). Mid-wizard upload ' +
                    'support arrives in a later task — for now, restart the ' +
                    'CLI with --photos pointing at all your reference photos.'
                );
            });
        }
    }

    function advancePhase(fromId, toId) {
        var from = document.getElementById('phase-' + fromId);
        var to = document.getElementById('phase-' + toId);
        if (from) from.hidden = true;
        if (to) to.hidden = false;
    }

    function wirePhase2b() {
        var presetSelect = document.getElementById('phase-2b-preset');
        if (presetSelect) {
            populatePresetOptions(presetSelect);
            // Reveal the custom-anchors textarea only when the user picks
            // the Custom preset. Other presets generate anchors from the
            // long/short dimension inputs and don't need the textarea.
            presetSelect.addEventListener('change', function () {
                var customRow = document.getElementById('phase-2b-custom-anchors-row');
                if (customRow) customRow.hidden = (presetSelect.value !== 'custom');
            });
        }
        var nextBtn = document.getElementById('phase-2b-next');
        if (!nextBtn) return;
        nextBtn.addEventListener('click', function () {
            var error = commitFrameAndAdvance();
            var errorEl = document.getElementById('phase-2b-error');
            if (error) {
                if (errorEl) {
                    errorEl.textContent = error;
                    errorEl.hidden = false;
                }
                return;
            }
            if (errorEl) errorEl.hidden = true;
            advancePhase('2b', '2c');
        });
    }

    function populatePresetOptions(select) {
        Object.keys(FRAME_PRESETS).forEach(function (key) {
            var opt = document.createElement('option');
            opt.value = key;
            opt.textContent = FRAME_PRESETS[key].label;
            select.appendChild(opt);
        });
    }

    // Returns null on success (anchors committed to spatialState.frame),
    // or a human-readable error string. Validation is intentionally light —
    // the backend re-validates dimensions when /api/anchors is POSTed in
    // Phase 2c — but we want the user to see "fix this here" rather than
    // a back-button trip from a 400 response later.
    function commitFrameAndAdvance() {
        var presetEl = document.getElementById('phase-2b-preset');
        var longEl = document.getElementById('phase-2b-long-edge');
        var shortEl = document.getElementById('phase-2b-short-edge');
        var notesEl = document.getElementById('phase-2b-notes');
        var customAnchorsEl = document.getElementById('phase-2b-custom-anchors');

        var preset = (presetEl || {}).value || '';
        var notes = (notesEl || {}).value || '';

        // Reject preset values that aren't in our table — defends against
        // a stale DOM choice surviving a refactor that drops a preset.
        var presetDef = FRAME_PRESETS[preset];
        if (!preset || !presetDef) {
            return 'Pick a frame preset before continuing.';
        }

        var longMm = readNumberInput(longEl);
        var shortMm = readNumberInput(shortEl);
        var anchors;

        if (preset === 'custom') {
            // Custom: parse the user-typed anchor definitions. Long/short
            // dimensions are optional in custom mode (anchors carry their
            // own coordinates, not derived from edge lengths).
            var customText = (customAnchorsEl || {}).value || '';
            var parsed;
            try {
                parsed = parseCustomAnchors(customText);
            } catch (err) {
                return 'Custom anchors: ' + err.message;
            }
            if (parsed.length < 1) {
                return 'Custom mode needs at least one anchor (format: id: x, y, z).';
            }
            anchors = parsed;
            // For custom mode, dimensions default to 0 if user left them
            // blank — they're informational only and shipped to the agent
            // alongside notes.
            if (!isFinite(longMm)) longMm = 0;
            if (!isFinite(shortMm)) shortMm = 0;
        } else {
            // Preset modes derive anchors from dimensions; both must be
            // positive and within the sane upper bound.
            if (!isFinite(longMm) || longMm <= 0 || longMm > MAX_DIMENSION_MM) {
                return 'Enter a positive long-edge length (mm), at most ' + MAX_DIMENSION_MM + '.';
            }
            if (!isFinite(shortMm) || shortMm <= 0 || shortMm > MAX_DIMENSION_MM) {
                return 'Enter a positive short-edge length (mm), at most ' + MAX_DIMENSION_MM + '.';
            }
            if (typeof presetDef.anchors !== 'function') {
                return 'Preset "' + preset + '" has no anchor generator (likely a bug).';
            }
            anchors = presetDef.anchors(longMm, shortMm);
        }

        window.spatialState.frame = {
            preset: preset,
            longMm: longMm,
            shortMm: shortMm,
            notes: notes,
            anchors: anchors,
        };
        return null;
    }

    // Read a numeric value from an <input type="number">. Prefers
    // valueAsNumber (parses strictly, returns NaN on invalid) over
    // parseFloat (which would silently accept "12abc" as 12).
    function readNumberInput(el) {
        if (!el) return NaN;
        if (typeof el.valueAsNumber === 'number') return el.valueAsNumber;
        return parseFloat(el.value);
    }

    // Parse the custom-anchor textarea. Each non-blank, non-comment
    // line must match ``id: x, y, z`` where id is a valid identifier
    // and x/y/z are finite numbers (mm). Throws on malformed lines.
    // Lines starting with '#' are comments.
    function parseCustomAnchors(text) {
        var anchors = [];
        var lines = text.split(/\r?\n/);
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line || line.charAt(0) === '#') continue;
            var match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^,]+)\s*$/);
            if (!match) {
                throw new Error('line ' + (i + 1) + ' malformed (expected "id: x, y, z"): ' + line);
            }
            var id = match[1];
            var x = parseFloat(match[2]);
            var y = parseFloat(match[3]);
            var z = parseFloat(match[4]);
            if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
                throw new Error('line ' + (i + 1) + ' has non-finite coordinate: ' + line);
            }
            anchors.push({ id: id, xyz: [x, y, z] });
        }
        return anchors;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
