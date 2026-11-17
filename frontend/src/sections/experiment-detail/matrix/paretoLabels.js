// Collision-free point labels for the Matrix frontier chart (R3 SVP-4.8,
// R2D: `#2E`/`#2D` and `#2C`/`#2B` overlapped). Pure: pixel positions in,
// label boxes out. Each label tries the slots below in order and takes the
// first that overlaps no placed label, no point and stays inside the plot;
// the far slots draw a leader line back to the point.

const CHAR_W = 6.6;
const LABEL_H = 13;
const POINT_R = 7;

// [dx, dy, anchor, leader]
export const LABEL_SLOTS = Object.freeze([
  [9, -7, "start", false],
  [-9, -7, "end", false],
  [9, 15, "start", false],
  [-9, 15, "end", false],
  [12, -24, "start", true],
  [-12, -24, "end", true],
  [12, 32, "start", true],
  [-12, 32, "end", true],
  [26, -40, "start", true],
  [-26, -40, "end", true],
]);

const boxOf = (p, [dx, dy, anchor], w) => {
  const x0 = anchor === "start" ? p.x + dx : p.x + dx - w;
  const yBase = p.y + dy;
  return { x0, x1: x0 + w, y0: yBase - LABEL_H + 2, y1: yBase + 2 };
};

const overlaps = (a, b) =>
  a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/**
 * Place labels.
 * @param {{id: string, x: number, y: number, label: string}[]} points
 * @param {{left: number, right: number, top: number, bottom: number}} bounds
 * @returns {Object<string, {x, y, anchor, leader: null|{x1,y1,x2,y2}}>}
 *   text position per id (`y` = baseline)
 */
export function placeLabels(points, bounds, obstacles = []) {
  const pts = (points ?? []).filter(
    (p) => Number.isFinite(p.x) && Number.isFinite(p.y),
  );
  const dots = pts.map((p) => ({
    x0: p.x - POINT_R,
    x1: p.x + POINT_R,
    y0: p.y - POINT_R,
    y1: p.y + POINT_R,
  }));
  // IT-R2 G2 P12: fixed marks (the Best value badge) count as placed.
  const placed = [...(obstacles ?? [])];
  const out = {};
  [...pts]
    .sort((a, b) => a.x - b.x || a.y - b.y)
    .forEach((p) => {
      const w = String(p.label ?? "").length * CHAR_W + 2;
      const inside = (b) =>
        !bounds ||
        (b.x0 >= bounds.left - 2 &&
          b.x1 <= bounds.right + 2 &&
          b.y0 >= bounds.top - 14 &&
          b.y1 <= bounds.bottom + 2);
      const slot = LABEL_SLOTS.find((s) => {
        const b = boxOf(p, s, w);
        return (
          inside(b) &&
          !placed.some((q) => overlaps(b, q)) &&
          !dots.some((d) => overlaps(b, d))
        );
      });
      // IT-R2 G2 P12: a label with no free slot is hidden (the point's
      // tooltip names it), never drawn over another label.
      if (!slot) {
        out[p.id] = null;
        return;
      }
      const b = boxOf(p, slot, w);
      placed.push(b);
      const [dx, dy, anchor, leader] = slot;
      out[p.id] = {
        x: p.x + dx,
        y: p.y + dy,
        anchor,
        leader: leader
          ? {
              x1: p.x,
              y1: p.y,
              x2: anchor === "start" ? b.x0 - 1 : b.x1 + 1,
              y2: dy < 0 ? b.y1 - 2 : b.y0 + 2,
            }
          : null,
        box: b,
      };
    });
  return out;
}

/**
 * IT-R4 G6 P9: the short name on a chart point — `#2F v3.1` for an agent
 * version, else the run's spoken name cut at a word (≤ 16 characters):
 * `#2D Claude Code`, `#2C LangGraph`. Pure.
 * @param {string} label  `#2F`
 * @param {{version?: string|null, name?: string|null}} who
 */
export function shortPointName(label, { version, name } = {}) {
  if (version) return `${label} ${String(version).replace(/\s*\(.*\)$/, "")}`;
  const words = String(name ?? "")
    .split(/\s+/)
    .filter(Boolean);
  let out = "";
  for (const w of words) {
    const next = out ? `${out} ${w}` : w;
    if (next.length > 16) break;
    out = next;
  }
  return [label, out].filter(Boolean).join(" ");
}
