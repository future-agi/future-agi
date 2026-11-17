// Path-distribution compare of two PathAggregates (pathModel.js).
// Pure; re-exported by pathModel.js.

/**
 * Path-distribution compare: A = the run on screen, B = `Compare with`.
 * `delta` = B share − A share in pts (1 dp, as displayed); `chip`
 * `new` = only B takes it (`New in #3A`), `gone` = only A (`Gone in #3A`).
 */
export function comparePaths(aggA, aggB) {
  const a = new Map((aggA?.paths ?? []).map((p) => [p.id, p]));
  const b = new Map((aggB?.paths ?? []).map((p) => [p.id, p]));
  const ids = [...new Set([...a.keys(), ...b.keys()])];
  const r1 = (v) => Math.round(v * 10) / 10;
  return ids
    .map((id) => {
      const pa = a.get(id) ?? null;
      const pb = b.get(id) ?? null;
      const shareA = pa?.share ?? 0;
      const shareB = pb?.share ?? 0;
      return {
        id,
        steps: (pa ?? pb).steps,
        a: pa,
        b: pb,
        shareA,
        shareB,
        delta: r1(r1(shareB * 100) - r1(shareA * 100)),
        failA: pa ? pa.fail_share : null,
        failB: pb ? pb.fail_share : null,
        chip: pa && !pb ? "gone" : pb && !pa ? "new" : null,
      };
    })
    .sort(
      (x, y) =>
        Math.max(y.shareA, y.shareB) - Math.max(x.shareA, x.shareB) ||
        (x.id < y.id ? -1 : 1),
    );
}
