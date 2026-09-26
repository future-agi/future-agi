// Geometry and label helpers for the per-metric pie charts (TH-6530).

import { isAdditiveAggregation } from "./widgetUtils";

const MIN_SLICE_ANGLE_DEG = 3;
const DONUT_HOLE_RATIO = 0.58;
const CENTER_FONT_MIN = 11;
const CENTER_FONT_MAX = 24;
const CENTER_CHAR_WIDTH_RATIO = 0.62;
const CONNECTOR_CHAR_WIDTH = 6.1;
const CONNECTOR_ELBOW = 18;
const EDGE_PADDING = 4;
const MIN_LABEL_CHARS = 6;
// The callout is two 11px lines drawn from `elbowY - 5`; keep it clear of the
// container's top and bottom edges.
const LABEL_TOP_PADDING = 18;
const LABEL_BOTTOM_PADDING = 20;

// Apex sweeps the ring over this many ms; a bbox read before it settles
// describes a partial arc.
export const DONUT_ANIMATION_MS = 400;
export const GEOMETRY_SETTLE_MS = DONUT_ANIMATION_MS + 60;

// Narrower than this and callouts collide with each other and the neighbouring
// donut, so slice names fall back to the shared legend and tooltips.
export const MIN_WIDTH_FOR_CONNECTORS = 340;

// The number shown in the donut hole, or null when there isn't an honest one.
// A single slice is its own total whatever the aggregation; beyond that, only
// additive aggregations add up to a real quantity.
export const getCenterValue = ({ aggregation, slices }) => {
  if (!slices?.length) return null;
  if (slices.length === 1) return slices[0].value;
  if (!isAdditiveAggregation(aggregation)) return null;
  return slices.reduce((a, s) => a + s.value, 0);
};

export const fitCenterFontSize = (text, radius) => {
  if (!text || !radius) return CENTER_FONT_MAX;
  const usable = radius * 2 * DONUT_HOLE_RATIO * 0.86;
  const ideal = usable / (text.length * CENTER_CHAR_WIDTH_RATIO);
  return Math.max(
    CENTER_FONT_MIN,
    Math.min(CENTER_FONT_MAX, Math.floor(ideal)),
  );
};

// Apex sizes the ring from its own layout, so the centre and radius shift with
// the container's aspect ratio. Read them off the rendered SVG instead of
// deriving them, or callouts end up pointing at empty space.
export const measureDonut = (container) => {
  const pie = container?.querySelector(".apexcharts-pie");
  if (!pie) return null;
  const pieRect = pie.getBoundingClientRect();
  if (!pieRect.width || !pieRect.height) return null;
  const hostRect = container.getBoundingClientRect();
  return {
    cx: pieRect.left - hostRect.left + pieRect.width / 2,
    cy: pieRect.top - hostRect.top + pieRect.height / 2,
    radius: Math.min(pieRect.width, pieRect.height) / 2,
    width: hostRect.width,
    height: hostRect.height,
  };
};

export const sameGeometry = (a, b) =>
  a === b ||
  (Boolean(a) &&
    Boolean(b) &&
    Math.round(a.cx) === Math.round(b.cx) &&
    Math.round(a.cy) === Math.round(b.cy) &&
    Math.round(a.radius) === Math.round(b.radius) &&
    Math.round(a.width) === Math.round(b.width) &&
    Math.round(a.height) === Math.round(b.height));

const truncateToWidth = (text, availablePx) => {
  const maxChars = Math.floor(availablePx / CONNECTOR_CHAR_WIDTH);
  if (maxChars >= text.length) return text;
  if (maxChars < MIN_LABEL_CHARS) return "";
  return `${text.slice(0, maxChars - 1).trimEnd()}…`;
};

export const buildConnectors = ({ geometry, slices, formatSlice }) => {
  const total = slices.reduce((a, s) => a + s.value, 0);
  if (!total) return [];
  const { cx, cy, radius: outerR, width, height } = geometry;
  const items = [];
  let cumAngle = -90;
  slices.forEach((slice) => {
    const sliceAngle = (slice.value / total) * 360;
    const midRad = ((cumAngle + sliceAngle / 2) * Math.PI) / 180;
    cumAngle += sliceAngle;
    if (sliceAngle < MIN_SLICE_ANGLE_DEG) return;

    const elbowDist = outerR + CONNECTOR_ELBOW;
    const elbowX = cx + elbowDist * Math.cos(midRad);
    const elbowY = Math.max(
      LABEL_TOP_PADDING,
      Math.min(
        height - LABEL_BOTTOM_PADDING,
        cy + elbowDist * Math.sin(midRad),
      ),
    );
    const isRight = Math.cos(midRad) >= 0;
    const endX = Math.max(
      EDGE_PADDING,
      Math.min(
        width - EDGE_PADDING,
        isRight ? elbowX + CONNECTOR_ELBOW : elbowX - CONNECTOR_ELBOW,
      ),
    );
    const textX = isRight ? endX + EDGE_PADDING : endX - EDGE_PADDING;
    const available = isRight
      ? width - textX - EDGE_PADDING
      : textX - EDGE_PADDING;

    // The value is the point of the callout, so it is never shortened; if it
    // cannot fit, drop the callout entirely.
    const line2 = formatSlice(slice.value);
    if (line2.length * CONNECTOR_CHAR_WIDTH > available) return;
    // The name alone. Callouts used to carry a letter counted within this
    // donut, which collided with the editor's summary strip lettering each
    // metric — "A" meant a metric there and a slice here, on one screen.
    const line1 = truncateToWidth(slice.name, available);
    if (!line1) return;

    items.push({
      edgeX: cx + outerR * Math.cos(midRad),
      edgeY: cy + outerR * Math.sin(midRad),
      elbowX,
      elbowY,
      endX,
      textX,
      isRight,
      line1,
      line2,
    });
  });
  return items;
};
