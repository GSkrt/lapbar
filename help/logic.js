.pragma library

// Splits Markdown into blocks the help window draws one by one: {type: "text", text} for Markdown, and
// {type: "image", alt, src} for a line that is only an image, ![alt](path). Qt's own Markdown text cannot place
// pictures where we want them, so they are pulled out here. Lines inside a ``` fence are never treated as images.
function splitMarkdown(markdown) {
    var lines = String(markdown).split("\n");
    var blocks = [];
    var buffer = [];
    var fenced = false;

    function flush() {
        var text = buffer.join("\n").replace(/^\n+|\n+$/g, "");
        if (text !== "") blocks.push({ type: "text", text: text });
        buffer = [];
    }

    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (/^```/.test(line)) fenced = !fenced;
        var image = fenced ? null : /^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/.exec(line);
        if (image) {
            flush();
            blocks.push({ type: "image", alt: image[1], src: image[2] });
        } else {
            buffer.push(line);
        }
    }
    flush();
    return blocks;
}

// An image address for the window: as it is if it has a scheme or starts with /, else relative to `base`.
function imageSource(base, src) {
    if (/^[a-z]+:\/\//i.test(src)) return src;
    if (src.charAt(0) === "/") return "file://" + src;
    return "file://" + String(base).replace(/\/+$/, "") + "/" + src;
}
