import { listScenarios } from "./scenarios";

// Resolve a predicate selection to concrete rows by paging the filtered list
// server-side (group_by="" so a page is a flat slice). `marked` holds the row
// ids the user ticked: in "include" mode those rows are taken, in "all" mode
// every matching row except them. `pick` projects each taken row — the amend
// route resolves drops by `name`, the run route reads `scenario_key`.
export const resolveScenarioSelection = async (
  jobId,
  { search, filters = {}, mode = "include", marked = [], pick = (r) => r },
) => {
  const wantAll = mode === "all";
  const markedSet = new Set(marked);
  const picked = [];
  let page = 1;
  let totalPages = 1;
  do {
    // eslint-disable-next-line no-await-in-loop
    const res = await listScenarios(jobId, {
      page,
      limit: 100,
      search,
      group_by: "",
      ...filters,
    });
    (res.results || []).forEach((r) => {
      if (wantAll !== markedSet.has(r.id)) picked.push(pick(r));
    });
    totalPages = res.total_pages || 1;
    page += 1;
  } while (page <= totalPages);
  return picked;
};

// Every scenario key in the suite — the run route needs keys, and an empty
// selection is refused, so "run all" names them explicitly.
export const listAllScenarioKeys = (jobId) =>
  resolveScenarioSelection(jobId, { mode: "all", pick: (r) => r.scenario_key });
