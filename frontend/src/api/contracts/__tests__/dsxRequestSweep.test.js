// L2 sweep (the capability contract): every call site, in the source, of every route
// dev serves strictly (strictRoutes.generated.js, derived from the backend's
// URL resolver by futureagi/model_hub/tests/test_dsx_contract_parity.py),
// checked against what the route's serializer declares (strictBody, from the
// generated contract). Nothing here lists routes or fields by hand.
//
// A site's body is read from the source: an object literal; a local
// `const x = {...}` (plus `x.key = ...` writes) or FormData (`x.append(...)`);
// the `data` of a DELETE's config; or, when the body is a parameter of the
// function that sends it (a react-query mutationFn, a helper), the literal
// each caller of that function passes. A new site that sends a key dev
// refuses fails this test until it is fixed or its surface is hidden.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  DSX_STRICT_ROUTES,
  acceptedFields,
  bodyDefinitionOf,
} from "../strictBody";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const AXIOS = readFileSync(join(SRC, "utils", "axios.js"), "utf8");

function sourceFiles(dir, out = []) {
  readdirSync(dir).forEach((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (["__tests__", "_mock", "generated", "node_modules"].includes(name))
        return;
      sourceFiles(p, out);
    } else if (/\.(js|jsx)$/.test(name) && !/\.(test|spec)\./.test(name)) {
      out.push(p);
    }
  });
  return out;
}
const FILES = sourceFiles(SRC).map((p) => ({
  file: relative(SRC, p),
  text: readFileSync(p, "utf8"),
}));

// `stop: (id) =>\n apiPath("/model-hub/experiments/v2/{experiment_id}/stop/"`
// -> `experiment.stop` (the property and the object that holds it).
function helpersOf(path) {
  const lines = AXIOS.split("\n");
  const out = [];
  lines.forEach((line, i) => {
    if (!line.includes(`"${path}"`)) return;
    let k = i;
    let key = null;
    for (; k >= Math.max(0, i - 3); k -= 1) {
      const m = /^(\s*)(\w+):/.exec(lines[k]);
      if (m) {
        key = { name: m[2], indent: m[1].length, line: k };
        break;
      }
    }
    if (!key) return;
    for (let j = key.line - 1; j >= 0; j -= 1) {
      const m = /^(\s*)(\w+):\s*\{\s*$/.exec(lines[j]);
      if (m && m[1].length < key.indent) {
        out.push(`${m[2]}.${key.name}`);
        return;
      }
    }
  });
  return [...new Set(out)];
}

const ROUTES = DSX_STRICT_ROUTES.map((r) => ({
  ...r,
  definition: bodyDefinitionOf(r.method, r.path),
  helpers: helpersOf(r.path),
}));

const OPEN = { "(": ")", "{": "}", "[": "]" };

// From an opening bracket: the balanced text (strings skipped).
function balanced(text, start) {
  const close = OPEN[text[start]];
  if (!close) return null;
  const stack = [];
  let quote = null;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (OPEN[ch]) stack.push(OPEN[ch]);
    else if (ch === stack[stack.length - 1]) {
      stack.pop();
      if (!stack.length) return text.slice(start, i + 1);
    }
  }
  return null;
}

// The top-level comma-separated parts of `(a, b, {c})` or `{a, b: 1}`.
function parts(group) {
  const out = [];
  let depth = 0;
  let quote = null;
  let token = "";
  for (let i = 1; i < group.length - 1; i += 1) {
    const ch = group[i];
    if (quote) {
      token += ch;
      if (ch === "\\") token += group[(i += 1)];
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    if (OPEN[ch]) depth += 1;
    if (")}]".includes(ch)) depth -= 1;
    if (depth === 0 && ch === ",") {
      out.push(token.trim());
      token = "";
    } else token += ch;
  }
  if (token.trim()) out.push(token.trim());
  return out;
}

// Top-level keys of an object literal `{ a: 1, b, ...c }`.
function literalKeys(obj) {
  const keys = [];
  const spreads = [];
  parts(obj).forEach((t) => {
    if (t.startsWith("...")) spreads.push(t.slice(3).trim());
    else {
      const m = /^["']?([A-Za-z_$][\w$]*)["']?\s*(:|$|\()/.exec(t);
      if (m) keys.push(m[1]);
    }
  });
  return { keys, spreads };
}

const IDENT = /^[A-Za-z_$][\w$]*$/;
const lineOf = (text, index) => text.slice(0, index).split("\n").length;

// The keys a body expression sends, read from the file; null when unknown.
function bodyOf(text, expr, at, depth = 0) {
  const e = String(expr ?? "").trim();
  if (!e || depth > 3) return null;
  if (e.startsWith("{")) {
    const { keys, spreads } = literalKeys(e);
    const out = new Set(keys);
    let open = false;
    spreads.forEach((s) => {
      const inner = bodyOf(text, s, at, depth + 1);
      if (inner?.keys) inner.keys.forEach((k) => out.add(k));
      if (!inner || inner.open) open = true;
    });
    return { keys: [...out], open, how: "literal" };
  }
  if (IDENT.test(e)) return identBody(text, e, at, depth);
  // A call whose first argument is the body (withRerunScope({...}, scope)).
  const call = /^([\w$.]+)\s*\(/.exec(e);
  if (call) {
    const args = balanced(e, call[0].length - 1);
    const first = args ? parts(args)[0] : null;
    if (first?.startsWith("{")) {
      const inner = bodyOf(text, first, at, depth + 1);
      return inner && { ...inner, open: true, how: `${call[1]}(literal)` };
    }
  }
  return null;
}

function identBody(text, name, at, depth) {
  const before = text.slice(0, at);
  // const x = { ... }  /  const x = new FormData()
  const decl = [
    ...before.matchAll(
      new RegExp(`\\b(?:const|let|var)\\s+${name}\\s*=\\s*`, "g"),
    ),
  ].pop();
  if (decl) {
    const start = decl.index + decl[0].length;
    if (text[start] === "{") {
      const obj = balanced(text, start);
      const inner = obj && bodyOf(text, obj, at, depth + 1);
      if (inner) {
        const writes = [
          ...text.matchAll(new RegExp(`\\b${name}\\.(\\w+)\\s*=[^=]`, "g")),
        ].map((m) => m[1]);
        return {
          keys: [...new Set([...inner.keys, ...writes])],
          open: inner.open,
          how: "const",
        };
      }
    }
    if (/^new\s+FormData\s*\(/.test(text.slice(start, start + 20))) {
      const keys = [
        ...text.matchAll(
          new RegExp(`\\b${name}\\.(?:append|set)\\(\\s*["'](\\w+)["']`, "g"),
        ),
      ].map((m) => m[1]);
      return { keys: [...new Set(keys)], open: false, how: "formdata" };
    }
  }
  return paramBody(text, name, at, depth);
}

// `name` is a parameter of the function around `at`: the body each caller
// of that function passes.
function paramBody(text, name, at, depth) {
  const heads = [
    ...text
      .slice(0, at)
      .matchAll(
        /(?:\(([^()]*)\)|\b([A-Za-z_$][\w$]*))\s*=>|function\s*[\w$]*\s*\(([^()]*)\)/g,
      ),
  ];
  for (let h = heads.length - 1; h >= 0; h -= 1) {
    const head = heads[h];
    const params = (head[1] ?? head[2] ?? head[3] ?? "").trim();
    const restOf = new RegExp(`\\.\\.\\.\\s*${name}\\b`).test(params);
    const plain = parts(`(${params})`).map((p) => p.replace(/=.*$/, "").trim());
    const pos = plain.indexOf(name);
    if (pos < 0 && !restOf) continue;
    const omit = restOf
      ? [...params.matchAll(/([A-Za-z_$][\w$]*)\s*[,}:=]/g)].map((m) => m[1])
      : [];
    const callers = callersOf(text, head.index);
    if (!callers) return null;
    const keys = new Set();
    let open = false;
    let n = 0;
    callers.forEach(({ args, index }) => {
      const arg = restOf ? args[0] : args[pos];
      const inner = arg == null ? null : bodyOf(text, arg, index, depth + 1);
      if (!inner) {
        open = true;
        return;
      }
      n += 1;
      inner.keys.filter((k) => !omit.includes(k)).forEach((k) => keys.add(k));
      if (inner.open) open = true;
    });
    if (!n) return null;
    return { keys: [...keys], open, how: `callers(${n})` };
  }
  return null;
}

// The calls of the function whose head is at `headAt`: a react-query
// mutationFn's mutate/mutateAsync (or their aliases), or a named function.
function callersOf(text, headAt) {
  const lead = text.slice(Math.max(0, headAt - 200), headAt);
  let names = [];
  if (/mutationFn\s*:\s*(?:async\s*)?$/.test(lead)) {
    const um = text.slice(0, headAt).lastIndexOf("useMutation(");
    if (um < 0) return null;
    const decl = /(?:const|let)\s+(\{[^}]*\}|[\w$]+)\s*=\s*$/.exec(
      text.slice(Math.max(0, um - 300), um),
    );
    if (!decl) return null;
    if (decl[1].startsWith("{")) {
      parts(decl[1]).forEach((p) => {
        const m = /^(mutate|mutateAsync)\s*(?::\s*([\w$]+))?$/.exec(p);
        if (m) names.push(m[2] ?? m[1]);
      });
    } else {
      names = [`${decl[1]}.mutate`, `${decl[1]}.mutateAsync`];
      // `const { mutate: run } = preview;`
      const alias = new RegExp(
        `(?:const|let)\\s+(\\{[^}]*\\})\\s*=\\s*${decl[1]}\\s*;`,
      ).exec(text);
      if (alias)
        parts(alias[1]).forEach((p) => {
          const m = /^(mutate|mutateAsync)\s*:\s*([\w$]+)$/.exec(p);
          if (m) names.push(m[2]);
        });
    }
  } else {
    const m =
      /(?:const|let)\s+([\w$]+)\s*=\s*(?:useCallback\(\s*)?(?:async\s*)?$/.exec(
        lead,
      ) ??
      /\b([\w$]+)\s*:\s*(?:async\s*)?$/.exec(lead) ??
      /function\s+([\w$]+)\s*$/.exec(
        text.slice(Math.max(0, headAt - 60), headAt + 9),
      );
    if (!m) return null;
    names = [m[1]];
  }
  const out = [];
  names.forEach((n) => {
    const re = new RegExp(`(?<![\\w$.])${n.replace(".", "\\.")}\\s*\\(`, "g");
    let m;
    while ((m = re.exec(text))) {
      if (
        m.index === headAt ||
        /function\s+$/.test(text.slice(m.index - 9, m.index))
      )
        continue;
      const args = balanced(text, m.index + m[0].length - 1);
      if (args) out.push({ args: parts(args), index: m.index });
    }
  });
  return out.length ? out : null;
}

// Each call site of a route helper, the axios method that sends it and the
// keys of the body it sends.
function callSites(route) {
  const sites = [];
  FILES.forEach(({ file, text }) => {
    route.helpers.forEach((h) => {
      const re = new RegExp(
        `endpoints\\.(?:[\\w$]+\\.)*${h.replace(".", "\\.")}\\b`,
        "g",
      );
      let m;
      while ((m = re.exec(text))) {
        const lead = text.slice(Math.max(0, m.index - 80), m.index);
        const verb = /axios\s*\.\s*(get|post|put|patch|delete)\s*\(\s*$/.exec(
          lead,
        )?.[1];
        if (verb && verb !== route.method) continue;
        let i = m.index + m[0].length;
        if (text[i] === "(") {
          const args = balanced(text, i);
          if (!args) continue;
          i += args.length;
        }
        const after = /^\s*,\s*/.exec(text.slice(i));
        let expr = null;
        let via = verb;
        // `{ url: endpoints.x(id), body: {...} }` (a batch of posts).
        const named = !verb && /^\s*,\s*body:\s*/.exec(text.slice(i));
        if (named) {
          const start = i + named[0].length;
          expr = OPEN[text[start]]
            ? balanced(text, start)
            : /^[\w$.]+/.exec(text.slice(start))?.[0];
          via = "body";
        }
        // `const url = endpoints.x(id)` ... `axios.post(url, body)`.
        const urlVar =
          !verb && !named
            ? /(?:const|let)\s+([\w$]+)\s*=\s*$/.exec(lead)?.[1]
            : null;
        if (urlVar) {
          const use = new RegExp(
            `axios\\s*\\.\\s*(get|post|put|patch|delete)\\s*\\(\\s*${urlVar}\\b`,
          ).exec(text.slice(i));
          if (use && use[1] !== route.method) continue;
          if (use) {
            const call = balanced(text, i + use.index + use[0].indexOf("("));
            const args = call ? parts(call) : [];
            expr = args[1] ?? null;
            via = use[1];
            if (route.method === "delete") {
              const cfg = expr?.startsWith("{") ? parts(expr) : [];
              const data = cfg.find((p) => /^data\s*(:|$)/.test(p));
              expr = data ? data.replace(/^data\s*:?\s*/, "") || "data" : null;
            }
          }
        }
        if (verb && after) {
          const call = balanced(
            text,
            text.slice(0, m.index).lastIndexOf("(", m.index),
          );
          const args = call ? parts(call) : [];
          expr = args[1] ?? null;
          if (route.method === "delete") {
            const cfg = expr?.startsWith("{") ? parts(expr) : [];
            const data = cfg.find((p) => /^data\s*(:|$)/.test(p));
            expr = data ? data.replace(/^data\s*:?\s*/, "") || "data" : null;
          }
        }
        const body = via ? bodyOf(text, expr, m.index) : null;
        const accepted = acceptedFields(route.definition);
        sites.push({
          file,
          line: lineOf(text, m.index),
          helper: h,
          verb: via ?? null,
          keys: body?.keys ?? null,
          open: body?.open ?? true,
          how: body?.how ?? (via ? "unresolved" : "not an axios call"),
          refused: body ? body.keys.filter((k) => !accepted.includes(k)) : [],
        });
      }
    });
  });
  return sites;
}

const SITES = ROUTES.flatMap((r) =>
  callSites(r).map((s) => ({ ...s, route: `${r.method} ${r.path}` })),
);

// Literal keys dev refuses that stay in the source because their surface is
// hidden in a real build (the capability contract: R3 results / R5 analysis are not
// Tuesday). Owed with the R3/R5 lanes; listed so the sweep stays exact.
const HIDDEN_REFUSALS = {
  "sections/experiment-detail/ExperimentSummary/Analysis/ExperimentAnalysisView.jsx":
    ["eval_versions", "run_ids"],
  "sections/experiment-detail/results/headers/useEvalVersionDoor.jsx": [
    "eval_versions",
    "run_ids",
  ],
};

// Literal keys dev refuses that a real build never sends: the request is
// refused in the browser first, with its reason (prototype mode sends it to
// its mocks). Proven in api/develop/__tests__/experiment-requests.real.test.js.
const GUARDED_IN_REAL_BUILD = {
  // useRenameExperiment: renameSupported() is false on dev.
  "api/develop/experiment-list.js": ["description", "name"],
};

// Hugging Face sends the declared revision field on real requests.
const OTHER_LANE_REFUSALS = {};

// Keys dev's own code sends too: each file is unchanged from origin/dev
// (6fedc779e1), or, for EvaluationMappingForm.jsx, the same keys at the same
// call there. Not a DSX change; reported with the lane's result.
const DEV_SENDS_TOO = {
  "pages/dashboard/models/ModelConfig/ModelPerformanceConfigCard.jsx": [
    "metricId",
  ],
  "pages/dashboard/models/Performance/SaveReport.jsx": ["endDate", "startDate"],
  "pages/dashboard/settings/UserManagementV2/AllActionForm.jsx": [
    "workspace_access",
    "workspace_id",
    "ws_level",
  ],
  "sections/agents/CreateNewAgentDefinitionView.jsx": [
    "headers",
    "password",
    "token",
    "username",
  ],
  "sections/agents/useAgentConfigForm.js": [
    "headers",
    "password",
    "token",
    "username",
  ],
  "sections/common/EvaluationDrawer/EvaluationMappingForm.jsx": [
    "eval_template_ids",
    "save_as_template",
  ],
  "sections/develop-detail/Common/AddEvaluation/AddedEvaluationCard.jsx": [
    "user_eval_ids",
  ],
  "sections/evals/EvalDetails/EvalsConfig/EvalsConfigTab.jsx": [
    "check_internet",
    "choices_map",
    "criteria",
    "description",
    "error_localizer_enabled",
    "eval_tags",
    "multi_choice",
    "name",
    "required_keys",
  ],
  "sections/evals/EvalDetails/EvaluationConfigureForm.jsx": [
    "is_run",
    "log_ids",
    "save_as_template",
  ],
  "sections/test/TestEvaluationDrawer.jsx": ["evaluations_config"],
  "sections/workbench/createPrompt/Simulate/SimulationDetailView/SimulationEvaluationDrawer.jsx":
    ["evaluations_config"],
};

// Call sites on dev's model-hub strict routes whose body this sweep cannot
// read from the source: react-hook-form values (the AddColumn forms, the
// upload form), or bodies a module builds (the experiment bodies, pinned at
// runtime by Experiment/__tests__/serverBody.contract.test.js and
// api/develop/__tests__/experiment-requests.real.test.js). The DSX diff adds
// no top-level key to any of them (L2 fix round 1). Pinned so that a new
// unread site fails here until it is read or reviewed.
const UNREAD_MODEL_HUB_SITES = {
  // putStar sends POST/DELETE with no body. The source parser represents
  // omitted bodies as unread; both strict routes accept the empty request.
  "api/develop/dataset-org.js": 2,
  // T-XP exp.rerun: the classic append PUT sends the body appendVariantBody()
  // builds, {append_only, prompt_config}, both declared by ExperimentUpdateV2
  // and pinned by experiment-detail-classic/__tests__/classic-rerun.test.jsx.
  "api/develop/experiment-append.js": 1,
  "api/develop/experiment-create.js": 3,
  "api/develop/experiment-list.js": 1,
  "api/develop/experiment-run-actions.js": 2,
  "api/model/info.js": 2,
  "pages/dashboard/models/CreateModelModal.jsx": 1,
  "pages/dashboard/models/ModelConfig/ConfigureDefaultDataset.jsx": 1,
  "sections/common/EvaluationDrawer/EditCustomEvals.jsx": 1,
  "sections/common/EvaluationDrawer/EvaluationDrawer.jsx": 1,
  "sections/develop-detail/AddColumn/AddColumnApiCall/AddColumnApiCall.jsx": 1,
  "sections/develop-detail/AddColumn/Classification/Classification.jsx": 2,
  "sections/develop-detail/AddColumn/ConditionalNode/ConditionalNodeMainForm.jsx": 1,
  "sections/develop-detail/AddColumn/ConditionalNode/ConditionalNodeV2.jsx": 1,
  "sections/develop-detail/AddColumn/ExtractEntities/ExtractEntities.jsx": 2,
  "sections/develop-detail/AddColumn/ExtractJsonKey/ExtractJsonKey.jsx": 2,
  "sections/develop-detail/AddColumn/Retrieval/Retrieval.jsx": 2,
  "sections/develop-detail/Experiment/StepperComponentExperiment/EvaluationStepExperimentCreation.jsx": 1,
  "sections/develop/AddDatasetDrawer/useFileUploadLifecycle.js": 1,
  "sections/develop/AddRowDrawer/CreateSyntheticDataView.jsx": 1,
  "sections/evals/EvalDetails/EvalsConfig/EvalsConfigTab.jsx": 1,
  "sections/evals/hooks/useCompositeEval.js": 4,
  "sections/evals/hooks/useCreateEval.js": 1,
  "sections/evals/hooks/useEvalDetail.js": 1,
  "sections/evals/hooks/useEvalVersions.js": 1,
  "sections/evals/hooks/useGroundTruth.js": 1,
  "sections/evals/types/DagEvalDetail.jsx": 1,
  // L5 (merged): the Hugging Face config read's body is hfConfigBody(), which
  // sends {dataset_path} only on the real backend (hfLaunchModel.test.js).
  "sections/huggingface/HuggingFaceView.jsx": 1,
  "sections/model/optimize-detail/PromptTemplateResults.jsx": 1,
  "sections/prompt/NewPrompt/TopMenuOptions/ImportDataset.jsx": 1,
};

// POST deletes outside L2's files (the leaderboard is R1-backed, hidden in a
// real build by the capability contract). Fixed by the owed patch
// the local review artifact,
// which also empties this list.
const OWED_POST_DELETES = [];

const refusedByFile = () => {
  const refused = {};
  SITES.filter((s) => s.refused.length).forEach((s) => {
    refused[s.file] = [
      ...new Set([...(refused[s.file] ?? []), ...s.refused]),
    ].sort();
  });
  return refused;
};

describe("the DSX strict-route sweep", () => {
  it("walks every strict route the UI has a URL helper for", () => {
    const withHelpers = ROUTES.filter((r) => r.helpers.length);
    expect(ROUTES.length).toBe(DSX_STRICT_ROUTES.length);
    expect(withHelpers.length).toBeGreaterThan(8);
    expect(SITES.length).toBeGreaterThan(40);
  });

  it("reads the body of most call sites (the rest are listed)", () => {
    const read = SITES.filter((s) => s.keys);
    // A floor, so a parser regression that reads nothing fails loudly.
    expect(read.length / SITES.length).toBeGreaterThan(0.6);
    const unread = {};
    SITES.filter(
      (s) =>
        !s.keys && s.verb && s.route.split(" ")[1].startsWith("/model-hub/"),
    ).forEach((s) => {
      unread[s.file] = (unread[s.file] ?? 0) + 1;
    });
    expect(unread).toEqual(UNREAD_MODEL_HUB_SITES);
  });

  it("no call site sends a key dev refuses, outside hidden surfaces", () => {
    expect(refusedByFile()).toEqual({
      ...HIDDEN_REFUSALS,
      ...GUARDED_IN_REAL_BUILD,
      ...OTHER_LANE_REFUSALS,
      ...DEV_SENDS_TOO,
    });
  });

  it("reads the Column from path preview body through its mutate call", () => {
    const preview = SITES.filter(
      (s) =>
        s.file.endsWith("ColumnFromPathDialog.jsx") &&
        s.route.includes("/preview/"),
    );
    expect(preview.length).toBe(1);
    expect(preview[0].keys).toEqual(
      expect.arrayContaining(["column_id", "json_key"]),
    );
    expect(preview[0].refused).toEqual([]);
  });

  it("no generated-column edit sends `scope` (dev re-runs every row)", () => {
    const offenders = FILES.filter(({ text }) =>
      /\bscope:\s*rerunScope\b/.test(text),
    ).map((f) => f.file);
    expect(offenders).toEqual([]);
  });

  it("no experiment delete is a POST (dev's route answers DELETE only)", () => {
    const offenders = FILES.filter(({ text }) =>
      /axios\s*\.post\(\s*endpoints\.develop\.experiment\.delete\b/.test(text),
    ).map((f) => f.file);
    expect(offenders).toEqual(OWED_POST_DELETES);
  });

  it("the upload body is built by uploadBody.js, never by hand", () => {
    const hand = FILES.filter(
      ({ file, text }) =>
        /append\(\s*["'](column_roles|import_options)["']/.test(text) &&
        !file.endsWith("AddDatasetDrawer/uploadBody.js"),
    ).map((f) => f.file);
    // AddRowDrawer's own upload goes to the absent imports route (hidden).
    expect(hand.filter((f) => f.includes("AddDatasetDrawer"))).toEqual([]);
  });
});

// DSX_SWEEP_DUMP=<file>: the sites as JSON, for a reviewer.
const DUMP = globalThis.process?.env?.DSX_SWEEP_DUMP;
if (DUMP) writeFileSync(DUMP, JSON.stringify(SITES, null, 1));
