// How much is already built for a template — the stat footer on each tile.
//
// Closed form of the designer's derived-pack machinery: five packs per
// environment, one scenario per (tool × 4 variants), (rule × 3), (data-trap × 3),
// plus an edge and an adversarial pack that each take `depth` templates. Every
// pack template set is at least `depth` long, so both resolve to `depth`.
const DEPTH = { Starter: 3, Intermediate: 4, Advanced: 5, Expert: 6 };

const depthFor = (env) => DEPTH[env.difficulty] || 4;

const trapTables = (env) => (env.seed?.tables || []).filter((t) => t.note);

export const packStats = (env) => {
  const depth = depthFor(env);
  const scenarios =
    (env.tools?.length || 0) * 4 +
    (env.rules?.length || 0) * 3 +
    trapTables(env).length * 3 +
    depth * 2;
  return { packs: 5, scenarios };
};
