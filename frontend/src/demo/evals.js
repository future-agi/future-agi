/* Demo eval library for the hosted prototype: the system evals the
   simulation's presets stand for, in the list and detail shapes the eval
   picker reads. */
import {
  EVAL_CATALOG,
  defaultMappingFor,
  systemEvalFor,
} from "src/sections/simulate-v2/_mock/evals";

const CODE = new Set(["latency_check", "deterministic_evals"]);

const seen = new Set();
export const DEMO_EVALS = EVAL_CATALOG.flatMap((e, i) => {
  const name = systemEvalFor(e.id);
  if (!name || seen.has(name)) return [];
  seen.add(name);
  const keys = Object.keys(defaultMappingFor(e.id));
  const evalType = CODE.has(name) ? "code" : "llm";
  return [
    {
      id: name,
      template_id: name,
      name,
      description: e.blurb,
      template_type: "single",
      eval_type: evalType,
      output_type: e.threshold >= 1 ? "pass_fail" : "percentage",
      owner: "system",
      created_by_name: "Future AGI",
      tags: [e.category],
      version_count: 1,
      current_version: "V1",
      last_updated: new Date(Date.parse("2026-10-20T09:00:00Z") - i * 864e5).toISOString(),
      thirty_day_run_count: 0,
      required_keys: keys,
      requiredKeys: keys,
      pass_threshold: e.threshold,
      model: evalType === "llm" ? "turing_large" : null,
      instructions: evalType === "llm" ? `${e.blurb} Read {{${keys[0] || "output"}}} and score it.` : "",
      config: { required_keys: keys, pass_threshold: e.threshold, run_config: {} },
    },
  ];
});

/* The list endpoint's answer: search and paging as the API takes them. */
export const evalsPage = (q = {}) => {
  const search = (q.search || "").toLowerCase();
  const rows = DEMO_EVALS.filter((e) => !search || `${e.name} ${e.description}`.toLowerCase().includes(search));
  const size = Number(q.page_size) || 25;
  const page = Number(q.page) || 0;
  return { items: rows.slice(page * size, page * size + size), total: rows.length };
};

export const evalDetail = (path) => DEMO_EVALS.find((e) => path.includes(`/${e.id}/`)) || null;
