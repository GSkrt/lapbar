.pragma library

// Pure helpers for the chart window: colours, ticks, cursor lookup, zoom, formatting.
// No QML types in here, so it can be unit-tested with plain node (see tests/test_charts_logic.py).

// Categorical palette, validated with the dataviz validator (fixed slot order, one slot per metric,
// so a metric keeps its colour whichever series happen to be present).
var LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"];
var DARK  = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"];
var SLOT  = { altitude: 0, speed: 1, pace: 1, heartrate: 2, watts: 3, cadence: 4, temp: 5, grade: 6,
              fitness: 0, fatigue: 1, form: 2, load: 3 };   // fitness chart: fitness blue, fatigue orange (form and load are drawn by polarity / neutral)

function parseColor(text) {
    var s = String(text || "").replace("#", "");
    if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
    if (s.length === 8) s = s.slice(2);              // #aarrggbb
    if (s.length !== 6 || /[^0-9a-fA-F]/.test(s)) return null;
    return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}

function luminance(rgb) {
    function lin(c) { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
}

function isDark(bgHex) {
    var rgb = parseColor(bgHex);
    return rgb ? luminance(rgb) < 0.4 : true;
}

function seriesColor(key, dark) {
    var slot = SLOT[key];
    return (dark ? DARK : LIGHT)[slot === undefined ? 0 : slot];
}

function hex2(n) { var h = Math.max(0, Math.min(255, Math.round(n))).toString(16); return h.length < 2 ? "0" + h : h; }

// `t` of the way from `bg` to `fg` (0 = bg, 1 = fg), as #rrggbb.
function mix(fgHex, bgHex, t) {
    var f = parseColor(fgHex) || [255, 255, 255], b = parseColor(bgHex) || [0, 0, 0];
    return "#" + hex2(b[0] + (f[0] - b[0]) * t) + hex2(b[1] + (f[1] - b[1]) * t) + hex2(b[2] + (f[2] - b[2]) * t);
}

function rgba(hex, alpha) {
    var c = parseColor(hex) || [255, 255, 255];
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + alpha + ")";
}

// Round tick spacing (1, 2, 5 x 10^n) giving about `target` ticks over `range`.
function niceStep(range, target) {
    if (!(range > 0)) return 1;
    var raw = range / Math.max(1, target);
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var f = raw / mag;
    return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * mag;
}

function ticks(min, max, target) {
    var step = niceStep(max - min, target), out = [];
    // Decimal places needed to write a multiple of `step` exactly (0.25 -> 2), so no 0.15000000000000002.
    var places = Math.max(0, 1 - Math.floor(Math.log(step) / Math.LN10 + 1e-9));
    var first = Math.ceil(min / step - 1e-9);
    for (var i = first; i * step <= max + step * 1e-9; i++)
        out.push(Number((i * step).toFixed(places)));
    return out;
}

// Index of the sample nearest to x (xs ascending).
function indexAt(xs, x) {
    var lo = 0, hi = xs.length - 1;
    if (hi < 0) return -1;
    while (hi - lo > 1) {
        var mid = (lo + hi) >> 1;
        if (xs[mid] <= x) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
}

// Inclusive index range of the samples inside [v0, v1], widened by one on each side so lines reach the edges.
function visibleRange(xs, v0, v1) {
    var a = indexAt(xs, v0), b = indexAt(xs, v1);
    if (xs[a] > v0 && a > 0) a -= 1;
    if (xs[b] < v1 && b < xs.length - 1) b += 1;
    return [a, b];
}

function stats(values, i0, i1) {
    var min = Infinity, max = -Infinity, sum = 0, n = 0;
    for (var i = i0; i <= i1; i++) {
        var v = values[i];
        if (v === null || v === undefined) continue;
        if (v < min) min = v;
        if (v > max) max = v;
        sum += v; n++;
    }
    return n ? { min: min, max: max, avg: sum / n, count: n } : null;
}

function fmtPace(seconds) {
    var s = Math.round(seconds);
    var sec = s % 60;
    return Math.floor(s / 60) + ":" + (sec < 10 ? "0" : "") + sec;
}

function fmtValue(series, v) {
    if (v === null || v === undefined) return "–";
    if (series.format === "pace") return fmtPace(v);
    return Number(v).toFixed(series.decimals || 0);
}

// Axis tick labels: whole numbers lose their decimals ("20", not "20.0").
function fmtTick(series, v) {
    if (series.format === "pace") return fmtPace(v);
    return Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : Number(v).toFixed(series.decimals || 1);
}

// ---- dates: the fitness chart's x axis is whole days since 1970-01-01 (UTC)

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
var WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function fmtDate(day, long) {
    var t = new Date(Math.round(day) * 86400000);
    var s = t.getUTCDate() + " " + MONTHS[t.getUTCMonth()];
    return long ? WEEKDAYS[t.getUTCDay()] + " " + s + " " + t.getUTCFullYear() : s;
}

// Tick positions for a date axis: days for short spans, Mondays for weeks, month starts beyond that.
function dateTicks(min, max) {
    var span = max - min, out = [], d;
    if (span <= 21) {
        var step = span <= 10 ? 1 : 2;
        for (d = Math.ceil(min / step) * step; d <= max; d += step) out.push({ x: d, label: fmtDate(d) });
    } else if (span <= 100) {
        var every = span <= 60 ? 7 : 14;                                  // 1970-01-05 was a Monday: day 4
        for (d = Math.ceil((min - 4) / every) * every + 4; d <= max; d += every) out.push({ x: d, label: fmtDate(d) });
    } else {
        var months = span <= 200 ? 1 : (span <= 400 ? 2 : 3);
        var first = new Date(Math.round(min) * 86400000);
        var y = first.getUTCFullYear(), m = first.getUTCMonth();
        for (var guard = 0; guard < 60; guard++) {
            d = Date.UTC(y, m, 1) / 86400000;
            if (d >= min && d <= max) out.push({ x: d, label: m === 0 ? String(y) : MONTHS[m] });
            if (d > max) break;
            m += months;
            y += Math.floor(m / 12);
            m = m % 12;
        }
    }
    return out;
}

function fmtX(xMeta, x) {
    if (xMeta.unit === "date") return fmtDate(x, false);
    if (xMeta.unit === "min") {
        var total = Math.round(x * 60), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
        return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }
    return x.toFixed(x < 10 ? 2 : 1);
}

// Zoom the view [start, end] around `focus` by `factor` (<1 zooms in), staying inside [lo, hi].
function zoom(start, end, lo, hi, focus, factor, minSpan) {
    var span = end - start;
    var next = Math.max(minSpan, Math.min(hi - lo, span * factor));
    var frac = span > 0 ? (focus - start) / span : 0.5;
    var s = focus - frac * next;
    s = Math.max(lo, Math.min(hi - next, s));
    return [s, s + next];
}

// Slide the view by `delta` (in x units), staying inside [lo, hi].
function pan(start, end, lo, hi, delta) {
    var span = end - start;
    var s = Math.max(lo, Math.min(hi - span, start + delta));
    return [s, s + span];
}

// y-axis range for a set of values: the data extent with a little headroom, never a zero-height axis.
function yRange(values, i0, i1) {
    var st = stats(values, i0, i1);
    if (!st) return null;
    var pad = (st.max - st.min) * 0.08;
    if (pad === 0) pad = Math.max(1, Math.abs(st.max) * 0.05);
    return { min: st.min - pad, max: st.max + pad, dataMin: st.min, dataMax: st.max };
}

// ---- level of detail: what to draw for the visible part of a long series

// Segments of points, [x0, y0, x1, y1, ...] in data units, one segment per unbroken run of values, for the samples
// i0..i1 drawn across `columns` pixel columns that cover [v0, v1].
//  - At most two samples per column (zoomed in far enough to tell them apart): the actual samples, every one.
//  - More than that: per pixel column keep the first, the lowest, the highest and the last sample, in time order
//    (the "M4" reduction). Drawn as a line it looks the same as the full data at this width, and a one-sample spike
//    or dip cannot vanish the way it does when only every n-th sample is drawn. The cost follows the width of the
//    window, not the length of the ride.
function lod(xs, values, i0, i1, v0, v1, columns, nans) {
    var segments = [], seg = [], i, v, points = 0;                 // NaN, like null, is a gap
    if (i1 - i0 + 1 <= columns * 2 || !(v1 > v0)) {
        for (i = i0; i <= i1; i++) {
            v = values[i];
            if (v === null || v === undefined || v !== v) {
                if (seg.length) { segments.push(seg); seg = []; }
            } else { seg.push(xs[i], v); points++; }
        }
        if (seg.length) segments.push(seg);
        return { raw: true, segments: segments, points: points };
    }
    var scale = columns / (v1 - v0);
    // Samples just outside the view (the one on each side that lets lines reach the edges) are drawn as they are.
    var a = i0, end = i1 + 1;
    while (a < end && xs[a] < v0) {
        v = values[a];
        if (v === v && v !== null && v !== undefined) { seg.push(xs[a], v); points++; }
        a++;
    }
    var tail = end;
    while (tail > a && xs[tail - 1] >= v1) tail--;                  // samples at or beyond the right edge
    var edge = a;
    for (var c = 0; c < columns && a < tail; c++) {
        // the samples of column c are a..b-1: b is found by bisection, so the cost follows the width, not the count
        var limit = v0 + (c + 1) / scale, lo = a, hi = tail;
        if (xs[a] >= limit) continue;                               // nothing in this column
        while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (xs[mid] < limit) lo = mid; else hi = mid; }
        var b = xs[lo] < limit ? lo + 1 : lo;                       // one past the column's last sample
        if (b <= a) b = a + 1;
        if (nans && nans[b] - nans[a] > 0) {                        // a gap in this column: go sample by sample
            for (i = a; i < b; i++) {
                v = values[i];
                if (v !== v) { if (seg.length) { segments.push(seg); seg = []; } continue; }
                seg.push(xs[i], v); points++;
            }
        } else {
            var lowIdx = a, highIdx = a, low = values[a], high = low;
            for (i = a + 1; i < b; i++) {
                v = values[i];
                if (v < low) { low = v; lowIdx = i; } else if (v > high) { high = v; highIdx = i; }
            }
            points += emitColumn(seg, xs, values, a, lowIdx, highIdx, b - 1);
        }
        a = b;
    }
    for (i = a; i < end; i++) {                                     // what is left: the sample past the right edge
        v = values[i];
        if (v === null || v === undefined || v !== v) { if (seg.length) { segments.push(seg); seg = []; } }
        else { seg.push(xs[i], v); points++; }
    }
    if (seg.length) segments.push(seg);
    return { raw: false, segments: segments, points: points };
}

// One column's samples, in time order and without repeats: first, the lower and higher extreme, last.
function emitColumn(seg, xs, values, first, lo, hi, last) {
    var a = lo < hi ? lo : hi, b = lo < hi ? hi : lo, n = 1;
    seg.push(xs[first], values[first]);
    if (a !== first && a !== last) { seg.push(xs[a], values[a]); n++; }
    if (b !== a && b !== first && b !== last) { seg.push(xs[b], values[b]); n++; }
    if (last !== first) { seg.push(xs[last], values[last]); n++; }
    return n;
}

// Running count of gaps (NaN) in typed values, nans[i] = gaps before sample i, or null when there are none: lets lod()
// tell in one subtraction whether a column has a gap in it.
function gapCounts(values) {
    var out = new Int32Array(values.length + 1), any = false;
    for (var i = 0; i < values.length; i++) {
        var gap = values[i] !== values[i];
        if (gap) any = true;
        out[i + 1] = out[i] + (gap ? 1 : 0);
    }
    return any ? out : null;
}

// Lowest and highest y over a list of pieces returned by lod(), or null when there is nothing.
function extent(pieces) {
    var lo = Infinity, hi = -Infinity;
    for (var p = 0; p < pieces.length; p++) {
        var segs = pieces[p].segments;
        for (var s = 0; s < segs.length; s++) {
            var seg = segs[s];
            for (var k = 1; k < seg.length; k += 2) {
                if (seg[k] < lo) lo = seg[k];
                if (seg[k] > hi) hi = seg[k];
            }
        }
    }
    return lo <= hi ? { min: lo, max: hi } : null;
}

// The values as a Float64Array (a gap becomes NaN): reading those in a long loop is much faster than reading a
// JSON array that has nulls in it. Made once per series and kept.
function typed(values) {
    var out = new Float64Array(values.length);
    for (var i = 0; i < values.length; i++) {
        var v = values[i];
        out[i] = (v === null || v === undefined) ? NaN : v;
    }
    return out;
}
