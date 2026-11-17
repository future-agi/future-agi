// R2D-41: every pair of the first 8 run colours stays distinguishable for
// colour-blind viewers. Simulation: Machado, Oliveira & Fernandes (2009),
// severity 1.0, applied in linear sRGB; distance: CIEDE2000 in CIELAB (D65).
import { describe, expect, it } from "vitest";
import { RUN_PALETTE, runColorAt, runColorMap } from "../runColors";

const M = {
  normal: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ],
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
};
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const sim = (rgb, m) => {
  const l = rgb.map(lin);
  return m.map((r) =>
    Math.min(1, Math.max(0, r[0] * l[0] + r[1] * l[1] + r[2] * l[2])),
  );
};
const lab = ([r, g, b]) => {
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const X = f((r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047);
  const Y = f(r * 0.2126 + g * 0.7152 + b * 0.0722);
  const Z = f((r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883);
  return [116 * Y - 16, 500 * (X - Y), 200 * (Y - Z)];
};
function de2000([L1, a1, b1], [L2, a2, b2]) {
  const rad = Math.PI / 180;
  const C1 = Math.hypot(a1, b1);
  const C2 = Math.hypot(a2, b2);
  const Cb = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)));
  const a1p = a1 * (1 + G);
  const a2p = a2 * (1 + G);
  const C1p = Math.hypot(a1p, b1);
  const C2p = Math.hypot(a2p, b2);
  const hue = (x, y) => (Math.atan2(y, x) / rad + 360) % 360;
  const h1p = hue(a1p, b1);
  const h2p = hue(a2p, b2);
  let dhp = h2p - h1p;
  if (C1p * C2p === 0) dhp = 0;
  else if (dhp > 180) dhp -= 360;
  else if (dhp < -180) dhp += 360;
  const dLp = L2 - L1;
  const dCp = C2p - C1p;
  const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin((dhp * rad) / 2);
  const Lbp = (L1 + L2) / 2;
  const Cbp = (C1p + C2p) / 2;
  let hbp = h1p + h2p;
  if (C1p * C2p !== 0) {
    if (Math.abs(h1p - h2p) > 180) hbp += hbp < 360 ? 360 : -360;
    hbp /= 2;
  }
  const T =
    1 -
    0.17 * Math.cos((hbp - 30) * rad) +
    0.24 * Math.cos(2 * hbp * rad) +
    0.32 * Math.cos((3 * hbp + 6) * rad) -
    0.2 * Math.cos((4 * hbp - 63) * rad);
  const dt = 30 * Math.exp(-(((hbp - 275) / 25) ** 2));
  const RC = 2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7));
  const SL = 1 + (0.015 * (Lbp - 50) ** 2) / Math.sqrt(20 + (Lbp - 50) ** 2);
  const SC = 1 + 0.045 * Cbp;
  const SH = 1 + 0.015 * Cbp * T;
  const RT = -Math.sin(2 * dt * rad) * RC;
  return Math.sqrt(
    (dLp / SL) ** 2 +
      (dCp / SC) ** 2 +
      (dHp / SH) ** 2 +
      RT * (dCp / SC) * (dHp / SH),
  );
}

const minDE = (colors) => {
  let worst = Infinity;
  Object.values(M).forEach((m) => {
    for (let i = 0; i < colors.length; i += 1)
      for (let j = i + 1; j < colors.length; j += 1)
        worst = Math.min(
          worst,
          de2000(lab(sim(hex(colors[i]), m)), lab(sim(hex(colors[j]), m))),
        );
  });
  return worst;
};

describe("run colours are colour-blind safe", () => {
  it("light and dark: every pair ΔE2000 ≥ 10 (normal, deutan, protan)", () => {
    expect(RUN_PALETTE.light).toHaveLength(8);
    expect(minDE(RUN_PALETTE.light)).toBeGreaterThanOrEqual(10);
    expect(minDE(RUN_PALETTE.dark)).toBeGreaterThanOrEqual(10);
  });
  it("the R2 pink/blue pair failed (regression guard for the check itself)", () => {
    expect(minDE(["#D65EEC", "#559FF7"])).toBeLessThan(10);
  });
  it("keeps the API: baseline first, dark variant attached, 8 distinct", () => {
    const m = runColorMap(["b", "a", "c"], "a", { mode: "dark" });
    expect(m.a.index).toBe(0);
    expect(m.a.solid).toBe(RUN_PALETTE.dark[0]);
    expect(runColorAt(7).dark.solid).toBe("#FFCC1A");
    expect(new Set(RUN_PALETTE.light).size).toBe(8);
    expect(runColorAt(1).fg).toBe("#1A1A1A");
    expect(runColorAt(0).fg).toBe("#FFFFFF");
  });
});
