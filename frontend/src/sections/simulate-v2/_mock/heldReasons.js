/**
 * Why a held-back scenario won't run, in plain words first (handover: the
 * readiness reasons quote check and tool names). The check's own text stays
 * one click away as `detail`.
 */

const lower = (t) => String(t || "").replace(/^./, (c) => c.toLowerCase());

const PLAIN = {
  connection: (area) =>
    `The agent couldn't handle ${lower(area)} when we last checked it.`,
  rebuild: () =>
    "A tool it uses changed on the agent, so the environment needs rebuilding for it.",
  agent: (area) => `The agent doesn't support ${lower(area)} yet.`,
  release: () =>
    "A tool it uses goes to production, so it stays out of simulated runs.",
  test: (area) => `${area} hasn't been checked for this agent yet.`,
};

/** `blocked` = one compat.scope.blocked entry; `rows` = compat.rows. */
export const heldReasonOf = (blocked, rows = []) => {
  const why = rows.filter(
    (r) => r.blocks && (r.affected || []).includes(blocked.id),
  );
  const first = why[0];
  /* A tool still wired to production: say that, not the area's name. */
  const prod = String(first?.detail || "").match(
    /([a-z][a-z0-9_]*) still reaches production/,
  );
  const plain = prod
    ? `${prod[1]} still points at production, so it can't run against the test world.`
    : first
      ? (
          PLAIN[first.repair] ||
          ((area) => `The agent didn't pass ${lower(area)} on its last check.`)
        )(first.label)
      : "It can't run on this agent and environment.";
  return {
    plain: why.length > 1 ? `${plain} (+${why.length - 1} more)` : plain,
    detail: why
      .map((r) => `${r.label}: ${r.detail}`)
      .filter(Boolean)
      .join("\n"),
  };
};

/** "A, B and 3 more" — names, never keys. */
export const nameList = (names, max = 3) => {
  names = names.map((n) => String(n || "").replace(/\.+$/, ""));
  const shown = names.slice(0, max);
  const rest = names.length - shown.length;
  if (rest > 0) return `${shown.join(", ")} and ${rest} more`;
  if (shown.length < 2) return shown.join("");
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
};
