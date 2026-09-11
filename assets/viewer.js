// viewer.js — shared viewer logic for OCR review pages
// Expects page to define: REGIONS, imgW, PAGE_NUM, TOTAL_PAGES, IMG_URL

const COLORS = {
  doc_title:       { bg: "rgba(180,60,60,0.12)", hover: "rgba(180,60,60,0.22)", border: "rgba(180,60,60,0.5)" },
  paragraph_title: { bg: "rgba(60,90,160,0.12)", hover: "rgba(60,90,160,0.22)", border: "rgba(60,90,160,0.5)" },
  text:            { bg: "rgba(80,120,60,0.10)", hover: "rgba(80,120,60,0.20)", border: "rgba(80,120,60,0.4)" },
  figure_title:    { bg: "rgba(160,110,40,0.12)", hover: "rgba(160,110,40,0.22)", border: "rgba(160,110,40,0.5)" },
};

// --- OpenSeadragon ---
const viewer = OpenSeadragon({
  id: "viewer",
  prefixUrl: "https://cdn.jsdelivr.net/npm/openseadragon@4.1/build/openseadragon/images/",
  tileSources: { type: "image", url: IMG_URL },
  showNavigationControl: true,
  maxZoomPixelRatio: 4,
  animationTime: 0.3,
  gestureSettingsMouse: { clickToZoom: false },
});

const overlays = [];
const textBlocks = document.getElementById("text-pane").querySelectorAll("[data-idx]");

viewer.addHandler("open", function() {
  REGIONS.forEach((r, i) => {
    const [x1, y1, x2, y2] = r.bbox;
    const c = COLORS[r.label] || COLORS.text;
    const el = document.createElement("div");
    el.className = "region-overlay";
    el.dataset.idx = i;
    el.style.setProperty("--bg-color", c.bg);
    el.style.setProperty("--bg-hover", c.hover);
    el.style.setProperty("--border-color", c.border);

    const rect = new OpenSeadragon.Rect(x1/imgW, y1/imgW, (x2-x1)/imgW, (y2-y1)/imgW);
    viewer.addOverlay({ element: el, location: rect });
    overlays.push(el);
  });
  document.getElementById("viewer").classList.toggle("show-boxes", document.getElementById("toggleBoxes").checked);
});

// --- Click overlay on image -> highlight region ---
viewer.addHandler("canvas-click", function(event) {
  const webPoint = event.position;
  const viewportPoint = viewer.viewport.pointFromPixel(webPoint);
  // Find which region contains the click
  for (let i = REGIONS.length - 1; i >= 0; i--) {
    const [x1, y1, x2, y2] = REGIONS[i].bbox;
    const rx = x1 / imgW, ry = y1 / imgW;
    const rw = (x2 - x1) / imgW, rh = (y2 - y1) / imgW;
    if (viewportPoint.x >= rx && viewportPoint.x <= rx + rw &&
        viewportPoint.y >= ry && viewportPoint.y <= ry + rh) {
      highlightRegion(i);
      event.preventDefaultAction = true;
      return;
    }
  }
});

// --- Click text block -> zoom to region on image ---
textBlocks.forEach(el => {
  el.addEventListener("click", () => {
    const idx = parseInt(el.dataset.idx);
    highlightRegion(idx);
    const r = REGIONS[idx];
    const [x1, y1, x2, y2] = r.bbox;
    const cx = (x1 + x2) / 2 / imgW;
    const cy = (y1 + y2) / 2 / imgW;
    viewer.viewport.panTo(new OpenSeadragon.Point(cx, cy), false);
  });
});

let activeIdx = -1;
function highlightRegion(idx) {
  // Clear old
  overlays.forEach(o => o.classList.remove("active"));
  textBlocks.forEach(t => t.classList.remove("block-highlight"));
  // Set new
  if (idx === activeIdx) { activeIdx = -1; return; }
  activeIdx = idx;
  if (overlays[idx]) overlays[idx].classList.add("active");
  const tb = document.getElementById("text-pane").querySelector(`[data-idx="${idx}"]`);
  if (tb) {
    tb.classList.add("block-highlight");
    const pane = document.getElementById("text-pane");
    const top = tb.offsetTop - pane.offsetTop;
    pane.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }
}

// --- Controls ---
document.getElementById("toggleBoxes").addEventListener("change", (e) => {
  document.getElementById("viewer").classList.toggle("show-boxes", e.target.checked);
});

// --- Keyboard nav ---
document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft" && PAGE_NUM > 1)
    window.location = "page_" + String(PAGE_NUM - 1).padStart(2, "0") + ".html";
  if (e.key === "ArrowRight" && PAGE_NUM < TOTAL_PAGES)
    window.location = "page_" + String(PAGE_NUM + 1).padStart(2, "0") + ".html";
});

// --- Resize handle ---
const handle = document.getElementById("resize-handle");
const textPane = document.getElementById("text-pane");
let resizing = false;
handle.addEventListener("mousedown", (e) => {
  resizing = true; handle.classList.add("active");
  e.preventDefault();
});
document.addEventListener("mousemove", (e) => {
  if (!resizing) return;
  const w = document.body.clientWidth - e.clientX - 5;
  textPane.style.width = Math.max(200, Math.min(w, document.body.clientWidth * 0.6)) + "px";
});
document.addEventListener("mouseup", () => {
  resizing = false; handle.classList.remove("active");
});
