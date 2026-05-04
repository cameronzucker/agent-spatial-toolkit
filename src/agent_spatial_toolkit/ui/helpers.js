// helpers.js — client-side DOM utilities for the wizard (Task 1.D.2).
//
// Loaded by index.html via <script src="/ui/helpers.js">. Exposes a
// single namespace ``window.spatialUI`` with four utilities consumed
// by Phase 2a–2f wiring (Tasks 1.D.3–1.D.8):
//
//   captureClick(canvas, callback)
//     Click handler that normalizes CSS coords to canvas natural pixel
//     space, so a 2×-DPR scaled canvas reports the right click target.
//
//   loadImageToCanvas(url, canvas) -> Promise<{width, height}>
//     Fetch and draw an image; the canvas is sized to the image's
//     natural dimensions. Used in Phase 2c (anchor clicking) and
//     Phase 2d (feature clicking) to render the photo for click capture.
//
//   getEXIFFromUploaded(file, callback)
//     Minimal client-side EXIF parser for ``File`` objects (drag-dropped
//     photos). Extracts Make, Model, LensModel, FocalLength, and
//     FocalLengthIn35mmFilm. Calls callback(null) if the file is not
//     parseable JPEG/EXIF — intentional non-error: caller falls back to
//     manual entry. The server's intrinsics.py is the authoritative EXIF
//     parser; this client-side version exists so the wizard can show a
//     "we detected X" preview before a drag-dropped photo round-trips.
//
//   attachDragDrop(zone, onFiles)
//     Registers dragover / dragleave / drop listeners; on drop, calls
//     onFiles with an array of File objects. Adds a ``dragover`` class
//     to the zone during a hover for CSS styling.
//
// EXIF parser scope
// -----------------
// JPEG-only (PNG/HEIC return null cleanly via the SOI-marker check).
// Reads only the IFD0 + EXIF SubIFD; skips MakerNote and GPS IFDs.
// Single-segment APP1 only — multi-segment APP1 (rare) returns null.
// This is deliberately a "good enough for the preview" parser, not a
// general-purpose library; full coverage is the server's job.
(function () {
    'use strict';

    function captureClick(canvasElement, callback) {
        canvasElement.addEventListener('click', function (e) {
            var rect = canvasElement.getBoundingClientRect();
            // Skip when the canvas isn't laid out (display:none or
            // not-yet-attached). Without this guard, dividing by zero
            // produces NaN/Infinity coords that show up as "click did
            // nothing" debugging noise downstream.
            if (rect.width <= 0 || rect.height <= 0) return;
            // Scale CSS coords to canvas natural pixel coords. Required for
            // any canvas where width/height attribute differ from CSS box —
            // e.g., a 4032×3024 photo displayed at 800×600 still needs
            // clicks reported in the original pixel space.
            var x = (e.clientX - rect.left) * (canvasElement.width / rect.width);
            var y = (e.clientY - rect.top) * (canvasElement.height / rect.height);
            callback({ x: x, y: y });
        });
    }

    function loadImageToCanvas(url, canvas) {
        return new Promise(function (resolve, reject) {
            var img = new Image();
            img.onload = function () {
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                var ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                resolve({ width: img.naturalWidth, height: img.naturalHeight });
            };
            img.onerror = function () {
                reject(new Error('failed to load image: ' + url));
            };
            img.src = url;
        });
    }

    function getEXIFFromUploaded(file, callback) {
        var reader = new FileReader();
        reader.onload = function (e) {
            try {
                callback(parseExifFromArrayBuffer(e.target.result));
            } catch (_err) {
                callback(null);
            }
        };
        reader.onerror = function () { callback(null); };
        // 64KB is enough for EXIF on every camera I've seen; the segment
        // is right at the start of the file and rarely exceeds 32KB.
        reader.readAsArrayBuffer(file.slice(0, 64 * 1024));
    }

    function parseExifFromArrayBuffer(buf) {
        var view = new DataView(buf);
        if (view.byteLength < 4) return null;
        if (view.getUint16(0) !== 0xFFD8) return null; // not JPEG SOI
        var offset = 2;
        while (offset + 4 < view.byteLength) {
            var marker = view.getUint16(offset);
            // APP1 carries either EXIF or XMP. iPhones (since iOS 11) and
            // recent Android devices commonly write APP1/XMP BEFORE APP1/Exif,
            // so a return-on-first-APP1 short-circuit would silently skip
            // valid EXIF on those files. Verify the "Exif\0\0" identifier
            // and fall through to the segment-skip path if absent.
            if (marker === 0xFFE1 && offset + 10 < view.byteLength) {
                var exifId = String.fromCharCode(
                    view.getUint8(offset + 4),
                    view.getUint8(offset + 5),
                    view.getUint8(offset + 6),
                    view.getUint8(offset + 7)
                );
                if (exifId === 'Exif') {
                    var tiffStart = offset + 10;
                    // Need at least 8 bytes for the TIFF header (byte order
                    // + magic + IFD0 offset). Without this guard a truncated
                    // EXIF segment near the slice boundary would throw a
                    // RangeError that the caller-side try/catch swallows
                    // as "no EXIF" rather than the more accurate "EXIF
                    // present but malformed".
                    if (tiffStart + 8 > view.byteLength) return null;
                    var byteOrder = view.getUint16(tiffStart);
                    var le = (byteOrder === 0x4949);
                    var ifd0Offset = view.getUint32(tiffStart + 4, le);
                    return readIfd(view, tiffStart, ifd0Offset, le);
                }
                // APP1 but not EXIF — fall through to segment-skip below.
            }
            // Skip non-APP1 (or non-EXIF APP1) segment. Segment length
            // includes the 2 length bytes themselves but not the marker.
            var segLen = view.getUint16(offset + 2);
            offset += 2 + segLen;
        }
        return null;
    }

    // Tag IDs of interest:
    //   0x010F Make             ASCII (IFD0)
    //   0x0110 Model            ASCII (IFD0)
    //   0x8769 ExifIFDPointer   LONG  (IFD0 -> SubIFD offset)
    //   0xA434 LensModel        ASCII (SubIFD)
    //   0x920A FocalLength      RATIONAL (SubIFD)
    //   0xA405 FocalLengthIn35mmFilm SHORT (SubIFD)
    function readIfd(view, tiffStart, ifdOffset, le) {
        var ifdAbs = tiffStart + ifdOffset;
        var numEntries = view.getUint16(ifdAbs, le);
        var result = {
            make: null, model: null, lensModel: null,
            focalLength: null, focalLength35mm: null,
        };
        var exifSubIfdOffset = null;
        for (var i = 0; i < numEntries; i++) {
            var entryOff = ifdAbs + 2 + i * 12;
            var tag = view.getUint16(entryOff, le);
            var count = view.getUint32(entryOff + 4, le);
            var valueOff = entryOff + 8;
            if (tag === 0x010F) {
                result.make = readAscii(view, tiffStart, valueOff, count, le);
            } else if (tag === 0x0110) {
                result.model = readAscii(view, tiffStart, valueOff, count, le);
            } else if (tag === 0x8769) {
                exifSubIfdOffset = view.getUint32(valueOff, le);
            }
        }
        if (exifSubIfdOffset !== null) {
            var subIfdAbs = tiffStart + exifSubIfdOffset;
            var numSubEntries = view.getUint16(subIfdAbs, le);
            for (var j = 0; j < numSubEntries; j++) {
                var subEntryOff = subIfdAbs + 2 + j * 12;
                var subTag = view.getUint16(subEntryOff, le);
                var subCount = view.getUint32(subEntryOff + 4, le);
                var subValueOff = subEntryOff + 8;
                if (subTag === 0xA434) {
                    result.lensModel = readAscii(view, tiffStart, subValueOff, subCount, le);
                } else if (subTag === 0x920A) {
                    var dataOff = tiffStart + view.getUint32(subValueOff, le);
                    var num = view.getUint32(dataOff, le);
                    var den = view.getUint32(dataOff + 4, le);
                    result.focalLength = den ? num / den : null;
                } else if (subTag === 0xA405) {
                    result.focalLength35mm = view.getUint16(subValueOff, le);
                }
            }
        }
        return result;
    }

    function readAscii(view, tiffStart, valueOff, count, le) {
        // ASCII strings up to 4 bytes are inline in the value field;
        // longer strings store an offset (relative to TIFF start).
        var dataOff = (count <= 4) ? valueOff : tiffStart + view.getUint32(valueOff, le);
        var bytes = [];
        for (var i = 0; i < count; i++) {
            var c = view.getUint8(dataOff + i);
            if (c === 0) break; // EXIF ASCII strings are NUL-terminated
            bytes.push(c);
        }
        return String.fromCharCode.apply(null, bytes).trim();
    }

    function attachDragDrop(zone, onFiles) {
        zone.addEventListener('dragover', function (e) {
            e.preventDefault();
            zone.classList.add('dragover');
        });
        zone.addEventListener('dragleave', function () {
            zone.classList.remove('dragover');
        });
        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            zone.classList.remove('dragover');
            // Real `drop` events always have dataTransfer per HTML spec, but
            // synthetic events from tooling and some embedded contexts can
            // omit it. Guard so the helper degrades to onFiles([]) instead
            // of throwing.
            var files = e.dataTransfer ? Array.from(e.dataTransfer.files || []) : [];
            onFiles(files);
        });
    }

    window.spatialUI = {
        captureClick: captureClick,
        loadImageToCanvas: loadImageToCanvas,
        getEXIFFromUploaded: getEXIFFromUploaded,
        attachDragDrop: attachDragDrop,
    };
})();
