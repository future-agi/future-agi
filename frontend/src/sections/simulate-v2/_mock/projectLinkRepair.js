/*
  Seed 33: one Observe project per agent, and the one its traces land in.

  Two demo agents had drifted: Pickup dispatch agent was linked to a new,
  empty "pickup-dispatch-agent" project while its real traces (and the Error
  Feed issues it has fixes in review for) sit in "pickup-dispatch" — so those
  issues read "No agent" beside the agent's own fix. Billing copilot was
  linked to two projects. Each is put back on the project its traces land
  in; a link someone made to any other project is left alone.
*/
const REPAIRS = [
  { key: "repo:https://github.com/acme-inc/pickup-dispatch#app/graph.py:dispatch", keep: "pickup-dispatch", drop: ["pickup-dispatch-agent"] },
  { key: "repo:https://github.com/acme-inc/billing-copilot#app/graph.py:billing", keep: "billing-copilot", drop: ["billing-copilot-2"] },
];

export const withProjectLinkRepair = (state) => {
  const links = { ...(state?.projectLinks || {}) };
  let changed = false;
  REPAIRS.forEach(({ key, keep, drop }) => {
    const owned = Object.entries(links).filter(([, k]) => k === key).map(([p]) => p);
    if (!owned.length || owned.some((p) => !drop.includes(p) && p !== keep)) return;
    drop.forEach((p) => { if (links[p] === key) { delete links[p]; changed = true; } });
    if (links[keep] !== key && !links[keep]) { links[keep] = key; changed = true; }
  });
  return changed ? { ...state, projectLinks: links } : state;
};
