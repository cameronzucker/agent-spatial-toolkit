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

    // Lens catalog comes from GET /api/lens_catalog (Task 1.D.5 PR-α);
    // fetched at init and stored on window.spatialState.lensCatalog.

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
        // Fetch lens catalog + state in parallel. Catalog drives the lens
        // dropdown options in Phase 2a thumbnails (replaces the static
        // LENS_OPTIONS that lived here in 1.D.3); state drives initial
        // thumbnail render. Both must be available before renderThumbnails
        // because the lens select is built from the catalog.
        Promise.all([
            fetch('/api/lens_catalog').then(function (r) {
                if (!r.ok) throw new Error('GET /api/lens_catalog returned ' + r.status);
                return r.json();
            }),
            fetch('/api/state').then(function (r) {
                if (!r.ok) throw new Error('GET /api/state returned ' + r.status);
                return r.json();
            }),
        ])
            .then(function (results) {
                var catalogResp = results[0];
                var state = results[1];
                window.spatialState.lensCatalog = catalogResp.lenses || [];
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

        var lensOptions = lensCatalogToOptions(window.spatialState.lensCatalog);
        meta.appendChild(buildLabeledSelect('Lens', 'lens-select', lensOptions, function (val) {
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

    // Convert the /api/lens_catalog response into the {value, label} option
    // shape buildLabeledSelect expects. Always prepends a placeholder option
    // so a user-unselected dropdown has a stable default.
    function lensCatalogToOptions(catalog) {
        var opts = [{ value: '', label: '— Select lens —' }];
        (catalog || []).forEach(function (entry) {
            // Suffix non-resolvable entries so users see why a lens choice
            // might fail at /api/anchors time. Catalog server already includes
            // a `notes` field; we just hint here in the label.
            var label = entry.label;
            if (!entry.resolvable && entry.id !== 'exif:detected' && entry.id !== 'other') {
                label += ' (calibration required)';
            }
            opts.push({ value: entry.id, label: label });
        });
        return opts;
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
        wirePhase2c();
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

    // Phase 2c — per-photo anchor click capture + POST /api/anchors.
    // Renders one card per photo when Phase 2b's "Next" advances the wizard.
    // The card carries: canvas (photo loaded via spatialUI.loadImageToCanvas),
    // anchor checklist (one row per anchor from spatialState.frame.anchors),
    // "Solve pose" button (enabled when ≥3 anchors have pixel coords), and
    // a result area for the response.
    //
    // Per spec §5.3, minimum 3 non-collinear anchors per photo for solvePnP
    // to converge. We don't enforce non-collinearity client-side; the server
    // returns a 400 from PoseSolveError if the anchors are degenerate.
    function wirePhase2c() {
        // Hook into Phase 2b's "Next" — when the user advances from 2b, we
        // need to populate the photo cards (the frame anchors are now known).
        // We piggyback on the existing advancePhase by wrapping its call site:
        // Phase 2b's wirePhase2b already calls advancePhase('2b', '2c') after
        // commitFrameAndAdvance returns null. We don't have a hook there, so
        // use a MutationObserver on the phase-2c section's `hidden` attribute
        // to detect the transition. This keeps wirePhase2b unchanged.
        var phase2c = document.getElementById('phase-2c');
        if (!phase2c) return;
        var observer = new MutationObserver(function () {
            if (!phase2c.hidden) {
                renderPhase2cPhotos();
                observer.disconnect();  // one-shot: only render on first reveal
            }
        });
        observer.observe(phase2c, { attributes: true, attributeFilter: ['hidden'] });

        var nextBtn = document.getElementById('phase-2c-next');
        if (nextBtn) {
            nextBtn.addEventListener('click', function () { advancePhase('2c', '2d'); });
        }
    }

    function renderPhase2cPhotos() {
        var container = document.getElementById('phase-2c-photos');
        if (!container) return;
        clearChildren(container);
        var photoIds = Object.keys(window.spatialState.photos || {});
        if (photoIds.length === 0) {
            var empty = document.createElement('p');
            empty.className = 'placeholder';
            empty.textContent = 'No photos to annotate. Restart the CLI with --photos.';
            container.appendChild(empty);
            return;
        }
        var anchors = (window.spatialState.frame || {}).anchors || [];
        if (anchors.length === 0) {
            var noAnchors = document.createElement('p');
            noAnchors.className = 'error';
            noAnchors.textContent = 'No anchors declared in Phase 2b. Go back and pick a frame preset.';
            container.appendChild(noAnchors);
            return;
        }
        photoIds.forEach(function (photoId) {
            container.appendChild(buildPhase2cCard(photoId, anchors));
        });
    }

    function buildPhase2cCard(photoId, anchors) {
        var photo = window.spatialState.photos[photoId];
        var card = document.createElement('div');
        card.className = 'phase-2c-card';
        card.dataset.photoId = photoId;

        var name = document.createElement('div');
        name.className = 'phase-2c-photo-name';
        name.textContent = photoId;
        card.appendChild(name);

        var canvas = document.createElement('canvas');
        canvas.className = 'phase-2c-canvas';
        card.appendChild(canvas);

        var checklist = document.createElement('ul');
        checklist.className = 'phase-2c-checklist';
        card.appendChild(checklist);

        // Per-photo state held on the card's dataset / a closure
        var state = {
            currentAnchorId: null,
            clicks: {},  // anchor_id -> {x, y}
            imageSize: null,
        };
        anchors.forEach(function (anchor) {
            checklist.appendChild(buildAnchorChecklistRow(anchor, card, state));
        });

        var solveBtn = document.createElement('button');
        solveBtn.type = 'button';
        solveBtn.textContent = 'Solve pose';
        solveBtn.disabled = true;
        solveBtn.className = 'phase-2c-solve-btn';
        card.appendChild(solveBtn);

        var result = document.createElement('div');
        result.className = 'phase-2c-result';
        card.appendChild(result);

        // Load the photo into the canvas, then arm captureClick.
        if (window.spatialUI && photo && photo.url) {
            window.spatialUI.loadImageToCanvas(photo.url, canvas)
                .then(function (size) {
                    state.imageSize = [size.width, size.height];
                    window.spatialUI.captureClick(canvas, function (pt) {
                        if (!state.currentAnchorId) return;  // no anchor armed
                        state.clicks[state.currentAnchorId] = pt;
                        markChecklistRowComplete(checklist, state.currentAnchorId, pt);
                        state.currentAnchorId = null;
                        // Re-evaluate Solve button enablement
                        solveBtn.disabled = Object.keys(state.clicks).length < 3;
                    });
                })
                .catch(function (err) {
                    var status = document.createElement('p');
                    status.className = 'error';
                    status.textContent = 'Failed to load photo: ' + err.message;
                    card.appendChild(status);
                });
        }

        solveBtn.addEventListener('click', function () {
            postAnchors(photoId, photo, anchors, state, result, card);
        });

        return card;
    }

    function buildAnchorChecklistRow(anchor, card, state) {
        var li = document.createElement('li');
        li.className = 'phase-2c-checklist-row';
        li.dataset.anchorId = anchor.id;

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Click anchor: ' + anchor.id + ' (' + anchor.xyz.join(', ') + ' mm)';
        btn.className = 'phase-2c-anchor-btn';
        btn.addEventListener('click', function () {
            // Clear armed-class on all rows in this checklist; arm this one.
            var rows = card.querySelectorAll('.phase-2c-checklist-row');
            rows.forEach(function (r) { r.classList.remove('armed'); });
            li.classList.add('armed');
            state.currentAnchorId = anchor.id;
        });
        li.appendChild(btn);

        var status = document.createElement('span');
        status.className = 'phase-2c-anchor-status';
        status.textContent = '';
        li.appendChild(status);

        return li;
    }

    function markChecklistRowComplete(checklist, anchorId, pt) {
        var row = checklist.querySelector('[data-anchor-id="' + cssEscape(anchorId) + '"]');
        if (!row) return;
        row.classList.remove('armed');
        row.classList.add('complete');
        var status = row.querySelector('.phase-2c-anchor-status');
        if (status) status.textContent = ' → (' + Math.round(pt.x) + ', ' + Math.round(pt.y) + ')';
    }

    // Minimal CSS.escape polyfill for environments where window.CSS is absent.
    // Anchor IDs are constrained to ^[A-Za-z_][A-Za-z0-9_]*$ by Phase 2b's
    // parseCustomAnchors, so this is just defensive — the regex output is
    // always selector-safe — but it future-proofs against id schemes that
    // include a colon or hyphen.
    function cssEscape(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
        return String(value).replace(/[^A-Za-z0-9_-]/g, function (ch) {
            return '\\' + ch.charCodeAt(0).toString(16) + ' ';
        });
    }

    function postAnchors(photoId, photo, anchors, state, resultEl, card) {
        var clickedAnchors = anchors
            .filter(function (a) { return state.clicks[a.id]; })
            .map(function (a) {
                var pt = state.clicks[a.id];
                return {
                    pcb_xyz_mm: a.xyz,
                    pixel: [pt.x, pt.y],
                };
            });

        var body = {
            photo_id: photoId,
            anchors: clickedAnchors,
            image_size: state.imageSize,
        };
        // Pick lens path: explicit lens_id from Phase 2a, with EXIF dict
        // attached when the user picked the "exif:detected" sentinel.
        var lensId = (photo && photo.lens) || '';
        if (lensId) {
            body.lens_id = lensId;
            if (lensId === 'exif:detected' && photo.exif) {
                body.exif = {
                    focalLength35mm: photo.exif.focalLength35mm,
                    focalLength: photo.exif.focalLength,
                };
            }
        }
        // No lens_id picked → server returns 400; surface as a clear error.

        var solveBtn = card.querySelector('.phase-2c-solve-btn');
        if (solveBtn) solveBtn.disabled = true;
        clearChildren(resultEl);
        resultEl.className = 'phase-2c-result';
        resultEl.textContent = 'Solving pose…';

        fetch('/api/anchors', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(function (r) {
                return r.json().then(function (data) { return { ok: r.ok, data: data }; });
            })
            .then(function (resp) {
                clearChildren(resultEl);
                if (!resp.ok) {
                    resultEl.className = 'phase-2c-result error';
                    resultEl.textContent = 'Solve failed: ' + (resp.data.error || 'unknown error');
                    if (solveBtn) solveBtn.disabled = false;
                    return;
                }
                resultEl.className = 'phase-2c-result success';
                var rms = (resp.data.pose || {}).anchor_reprojection_rms_px;
                var suspect = resp.data.intrinsics_suspect;
                resultEl.textContent =
                    'Pose solved. RMS = ' + (rms != null ? rms.toFixed(2) : '?') +
                    ' normalized px. intrinsics_suspect: ' + (suspect ? 'yes' : 'no');
                // Record on spatialState so subsequent phases can read it.
                window.spatialState.photos[photoId].pose = resp.data.pose;
                window.spatialState.photos[photoId].intrinsicsSuspect = !!suspect;
                // Enable Next once at least one photo has a solved pose.
                var nextBtn = document.getElementById('phase-2c-next');
                if (nextBtn) nextBtn.disabled = false;
            })
            .catch(function (err) {
                clearChildren(resultEl);
                resultEl.className = 'phase-2c-result error';
                resultEl.textContent = 'Network error: ' + err.message;
                if (solveBtn) solveBtn.disabled = false;
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
