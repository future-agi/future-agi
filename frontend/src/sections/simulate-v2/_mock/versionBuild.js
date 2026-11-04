/*
  A new environment version being built after approved changes (a source's
  proposal, for now). Stored on the environment's state as
  { label, from, at, steps: [label], scenarios }, and read back against the
  clock, so leaving the page and coming back shows the same progress — and a
  build that finished while away finishes on the next visit.
*/

const STEP_MS = 3500;

/** What a version build will do, from the changes it carries. */
export function versionBuildOf({
  label,
  from,
  rules = 0,
  tables = [],
  scenarios = 0,
  now,
}) {
  const steps = [
    rules > 0 &&
      `Adding ${rules} rule${rules === 1 ? "" : "s"} to the contract`,
    tables.length > 0 && `Seeding the world from ${tables.join(", ")}`,
    scenarios > 0 &&
      `Writing and checking ${scenarios} scenario${scenarios === 1 ? "" : "s"}`,
    "Checking the new version starts",
  ].filter(Boolean);
  return { label, from, at: now || Date.now(), steps, scenarios };
}

/** Where a version build is: the step running now, and whether it's done. */
export function versionBuildProgress(build, now = Date.now()) {
  if (!build) return null;
  const step = Math.floor(Math.max(0, now - build.at) / STEP_MS);
  return {
    step: Math.min(step, build.steps.length - 1),
    done: step >= build.steps.length,
    leftMs: Math.max(0, build.at + build.steps.length * STEP_MS - now),
  };
}
