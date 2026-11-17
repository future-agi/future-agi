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
// each caller of that function passes. A write counts only for the object it
// is made on: Babel's scope at the write must resolve `x` to that same
// declaration, so a same-named object in another handler keeps its own keys.
// A new site that sends a key dev refuses fails this test until it is fixed
// or its surface is hidden.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";
import { describe, expect, it, vi } from "vitest";
import {
  ANSWER_TYPES,
  buildTemplatePayload,
} from "../../../sections/projects/LLMTracing/InstantEval/instantEvalPayloads";
import {
  EDIT_RUN_PROMPT_BODY,
  RERUN_OPERATION_BODY,
  withRerunScope,
} from "../../../sections/develop-detail/AddColumn/rerunScope";
import {
  DSX_STRICT_ROUTES,
  acceptedFields,
  bodyDefinitionOf,
  strictViolations,
} from "../strictBody";
import {
  findOpenApiEndpoint,
  validateContractedRequestConfig,
} from "../openapi-contract";

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

const traverse = traverseModule.default ?? traverseModule;
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

// Every `x.key = ...` and `x.append("key", ...)` / `x.set("key", ...)` of a
// source text, each with the start of the initializer of the declaration `x`
// resolves to in Babel's scope at that write (null for a parameter, an
// import or an unbound name). Keep the scopes too, so a request resolves its
// body to the same declaration. Parsed once per text.
const SOURCES = new Map();
function sourceOf(text) {
  if (SOURCES.has(text)) return SOURCES.get(text);
  const writes = [];
  const scopes = [];
  const calls = [];
  const urls = [];
  const add = (path, object, key, kind) =>
    writes.push({
      name: object.name,
      key,
      kind,
      initAt: path.scope.getBinding(object.name)?.path.node.init?.start ?? null,
    });
  const tree = parse(text, {
    sourceType: "module",
    plugins: ["jsx"],
    errorRecovery: true,
  });
  traverse(tree, {
    Scope(path) {
      scopes.push(path);
    },
    VariableDeclarator(path) {
      urls.push(path);
    },
    AssignmentExpression(path) {
      const { left, operator } = path.node;
      if (
        operator === "=" &&
        left.type === "MemberExpression" &&
        !left.computed &&
        left.object.type === "Identifier" &&
        left.property.type === "Identifier"
      )
        add(path, left.object, left.property.name, "assign");
    },
    CallExpression(path) {
      const { callee, arguments: args } = path.node;
      if (
        callee.type === "MemberExpression" &&
        !callee.computed &&
        callee.object.type === "Identifier" &&
        callee.object.name === "axios" &&
        /^(get|post|put|patch|delete)$/.test(callee.property.name)
      )
        calls.push(path);
      if (
        callee.type === "MemberExpression" &&
        !callee.computed &&
        callee.object.type === "Identifier" &&
        ["append", "set"].includes(callee.property.name) &&
        args[0]?.type === "StringLiteral" &&
        /^\w+$/.test(args[0].value)
      )
        add(path, callee.object, args[0].value, "append");
    },
  });
  const source = { writes, scopes, calls, urls };
  SOURCES.set(text, source);
  return source;
}

// Traversal visits outer scopes first: the last enclosing scope owns `at`.
function bindingAt(text, name, at) {
  const scope = sourceOf(text)
    .scopes.filter(({ node }) => node.start <= at && at < node.end)
    .pop()?.scope;
  return scope?.getBinding(name);
}

// The keys written onto the object `name` is declared with at `initAt`:
// writes made on another binding of the same name are not its keys.
const writesOn = (text, name, initAt, kind) =>
  sourceOf(text)
    .writes.filter(
      (w) => w.kind === kind && w.name === name && w.initAt === initAt,
    )
    .map((w) => w.key);

function identBody(text, name, at, depth) {
  const binding = bindingAt(text, name, at);
  // const x = { ... }  /  const x = new FormData(), in the request's scope.
  const decl = binding?.path.node;
  const init = decl?.id?.type === "Identifier" ? decl.init : null;
  if (init) {
    const start = init.start;
    if (text[start] === "{") {
      const obj = balanced(text, start);
      const inner = obj && bodyOf(text, obj, start, depth + 1);
      if (inner) {
        const writes = writesOn(text, name, start, "assign");
        return {
          keys: [...new Set([...inner.keys, ...writes])],
          open: inner.open,
          how: "const",
        };
      }
    }
    if (/^new\s+FormData\s*\(/.test(text.slice(start, start + 20))) {
      const keys = writesOn(text, name, start, "append");
      return { keys: [...new Set(keys)], open: false, how: "formdata" };
    }
  }
  return binding?.kind === "param" ? paramBody(text, name, at, depth) : null;
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

// A helper contributes a URL only as a value, never as a condition or as an
// argument to some other call. Logical operands can each supply the value.
function urlContains(node, start, end) {
  if (!node) return false;
  if (node.type === "ConditionalExpression")
    return [node.consequent, node.alternate].some((n) =>
      urlContains(n, start, end),
    );
  if (node.type === "LogicalExpression")
    return [node.left, node.right].some((n) => urlContains(n, start, end));
  const ref = node.type === "CallExpression" ? node.callee : node;
  return ref.start === start && ref.end === end;
}

function axiosCallOf(text, start, end) {
  const { calls, urls } = sourceOf(text);
  const direct = calls.find(({ node }) =>
    urlContains(node.arguments[0], start, end),
  );
  if (direct) return direct.node;
  const decl = urls.find(
    ({ node }) =>
      node.id.type === "Identifier" && urlContains(node.init, start, end),
  );
  return (
    decl &&
    calls.find((path) => {
      const arg = path.node.arguments[0];
      return (
        arg?.type === "Identifier" &&
        path.node.start > decl.node.end &&
        path.scope.getBinding(arg.name)?.path.node === decl.node
      );
    })?.node
  );
}

// Each call site of a route helper, the axios method that sends it and the
// keys of the body it sends.
function callSites(route, files = FILES) {
  const sites = [];
  files.forEach(({ file, text }) => {
    route.helpers.forEach((h) => {
      const re = new RegExp(
        `endpoints\\.(?:[\\w$]+\\.)*${h.replace(".", "\\.")}\\b`,
        "g",
      );
      let m;
      while ((m = re.exec(text))) {
        const call = axiosCallOf(text, m.index, m.index + m[0].length);
        const verb = call?.callee.property.name;
        if (verb && verb !== route.method) continue;
        let i = m.index + m[0].length;
        if (text[i] === "(") {
          const args = balanced(text, i);
          if (!args) continue;
          i += args.length;
        }
        const group =
          call && balanced(text, text.indexOf("(", call.callee.end));
        const args = group ? parts(group) : [];
        let expr = args[1] ?? null;
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
        if (call && route.method === "delete") {
          const cfg = expr?.startsWith("{") ? parts(expr) : [];
          const data = cfg.find((p) => /^data\s*(:|$)/.test(p));
          expr = data ? data.replace(/^data\s*:?\s*/, "") || "data" : null;
        }
        const body =
          call?.arguments.length === 1
            ? { keys: [], open: false, how: "no body" }
            : via
              ? bodyOf(text, expr, call?.start ?? m.index)
              : null;
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

// Literal keys dev refuses that remain in a hidden results surface. XP P04
// removed Summary's rescore write; the tests below pin its read-only replacement
// and the strict analysis query instead of retaining a nonexistent call site.
const HIDDEN_REFUSALS = {
  "sections/experiment-detail/results/headers/useEvalVersionDoor.jsx": [
    "eval_versions",
    "run_ids",
  ],
};

// Literal keys dev refuses that a real build never sends: the request is
// refused in the browser first, with its reason (prototype mode sends it to
// its mocks). Proven in api/develop/__tests__/experiment-requests.real.test.js.
// exp.rename: ExperimentUpdateV2 declares name and description, so the
// list's rename (api/develop/experiment-list.js) is no longer guarded.
const GUARDED_IN_REAL_BUILD = {};

// Hugging Face sends the declared revision field on real requests.
const OTHER_LANE_REFUSALS = {};

// Keys dev's own code sends too (not introduced by DSX). The binding-aware
// read resolves updateSimulateEval's ...payload in EvaluationMappingForm.jsx
// to onSubmit's outer payload. The old textual lookup borrowed the inner
// updateEval success-handler payload: eval_template_ids goes via runEval ->
// useRunEvalMutation -> experiment.runEvaluation (getEvalsList.jsx:76),
// which remains "not an axios call" here. Only save_as_template is refused
// at the read calls in EvaluationMappingForm.jsx.
const DEV_SENDS_TOO = {
  "pages/dashboard/models/ModelConfig/ModelPerformanceConfigCard.jsx": [
    "metricId",
  ],
  "pages/dashboard/models/Performance/SaveReport.jsx": ["endDate", "startDate"],
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
    "save_as_template",
  ],
  "sections/develop-detail/Common/AddEvaluation/AddedEvaluationCard.jsx": [
    "user_eval_ids",
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

// Instant eval (composed in from the c2.5 line, after this sweep was written)
// posts `buildTemplatePayload({ ...question, reads, unit, now })` to
// create-v2. The sweep reads that call's argument literal as the body, but the
// helper returns a different object: the argument names are its inputs, never
// wire keys. The test below pins that every key the helper returns is declared
// by the route's request definition, so nothing dev refuses travels.
const ARGUMENT_IS_NOT_THE_WIRE_BODY = {
  "sections/projects/LLMTracing/InstantEval/useInstantEval.js": [
    "now",
    "reads",
    "unit",
  ],
};

// Call sites on dev's model-hub strict routes whose body this sweep cannot
// read from the source: react-hook-form values (the AddColumn forms, the
// upload form), or bodies a module builds (the experiment bodies, pinned at
// runtime by Experiment/__tests__/serverBody.contract.test.js and
// api/develop/__tests__/experiment-requests.real.test.js). The DSX diff adds
// no top-level key to any of them (L2 fix round 1). Pinned so that a new
// unread site fails here until it is read or reviewed.
const UNREAD_MODEL_HUB_SITES = {
  // The version create and update (PATCH) mutations forward a caller-supplied
  // body; the PATCH became a strict route when its serializer was published.
  "api/develop/dataset-versions.js": 2,
  // T-XP exp.rerun: the classic append PUT sends the body appendVariantBody()
  // builds, {append_only, prompt_config}, both declared by ExperimentUpdateV2
  // and pinned by experiment-detail-classic/__tests__/classic-rerun.test.jsx.
  "api/develop/experiment-append.js": 1,
  "api/develop/experiment-create.js": 3,
  "api/develop/experiment-list.js": 1,
  "api/develop/experiment-run-actions.js": 2,
  // The result-copy mutation forwards a caller's body after removing targetId.
  "api/develop/run-results.js": 1,
  // The details URL is used by SWR, not another scope's axios call.
  "api/model/info.js": 1,
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
  // b.baseline comes from promoteBodies(), which returns {run_id} for this body.
  "sections/develop-detail/DatasetOptimization/OptimizationTrialRuns.jsx": 1,
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
  // Both URL branches post a conditionally initialized payload.
  "sections/evals/utils/evalExecution.js": 2,
  // L5 (merged): the Hugging Face config read's body is hfConfigBody(), which
  // sends {dataset_path} only on the real backend (hfLaunchModel.test.js).
  "sections/huggingface/HuggingFaceView.jsx": 1,
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
    expect(Object.keys(UNREAD_MODEL_HUB_SITES)).toHaveLength(30);
    expect(unread).toEqual(UNREAD_MODEL_HUB_SITES);
  });

  it("reads omitted bodies without mistaking trailing commas for arguments", () => {
    const helpers = ["sweep.target"];
    for (const method of ["post", "delete"]) {
      const route = { method, path: "/sweep/", helpers, definition: null };
      const sources = [
        `axios.${method}(endpoints.sweep.target);`,
        `axios.${method}(endpoints.sweep.target(id));`,
        `axios.${method}(\n endpoints.sweep.target(\n id,\n ),\n );`,
        `const url = endpoints.sweep.target(id); axios.${method}(url);`,
        `const url = endpoints.sweep.target(id); axios.${method}(url,);`,
      ];
      for (const text of sources) {
        const sites = callSites(route, [{ file: "fixture.js", text }]);
        expect(
          sites.map((s) => [s.verb, s.keys, s.open, s.how, s.refused]),
        ).toEqual([[method, [], false, "no body", []]]);
      }
    }
  });

  it("reads conditional and logical URL branches using the enclosing verb", () => {
    const helpers = ["sweep.left", "sweep.right"];
    for (const method of ["post", "delete"]) {
      const route = { method, path: "/sweep/", helpers, definition: null };
      for (const url of [
        "enabled ? endpoints.sweep.left(id) : endpoints.sweep.right",
        "endpoints.sweep.left(id) || endpoints.sweep.right",
        "endpoints.sweep.left(id) && endpoints.sweep.right",
        "endpoints.sweep.left(id) ?? endpoints.sweep.right",
      ]) {
        const body =
          method === "delete" ? "{ data: { flag: true } }" : "{ flag: true }";
        const text = `axios.${method}(${url}, ${body}, config);`;
        const sites = callSites(route, [{ file: "fixture.js", text }]);
        expect(
          sites.map((s) => [s.helper, s.verb, s.keys, s.open, s.how]),
        ).toEqual(helpers.map((h) => [h, method, ["flag"], false, "literal"]));
        expect(
          callSites({ ...route, method: "put" }, [
            { file: "fixture.js", text: `axios.get(${url});` },
          ]),
        ).toEqual([]);
      }
    }
  });

  it("resolves a conditional URL variable and body in their own scopes", () => {
    const helpers = ["sweep.left", "sweep.right"];
    const route = {
      method: "post",
      path: "/sweep/",
      helpers,
      definition: null,
    };
    const text = [
      "const url = enabled ? endpoints.sweep.left : endpoints.sweep.right(id);",
      '{ const url = "/elsewhere"; axios.get(url); }',
      "{ const payload = { flag: true }; axios.post(url, payload); }",
    ].join("\n");
    const sites = callSites(route, [{ file: "fixture.js", text }]);
    expect(sites.map((s) => [s.helper, s.verb, s.keys, s.open, s.how])).toEqual(
      helpers.map((h) => [h, "post", ["flag"], false, "const"]),
    );
  });

  it("does not borrow another function's same-named URL declaration", () => {
    const helpers = ["sweep.target"];
    const route = {
      method: "post",
      path: "/sweep/",
      helpers,
      definition: null,
    };
    const text = [
      "function read() {",
      "  const url = endpoints.sweep.target(id); useSWR(url);",
      "}",
      "function write() {",
      '  const url = "/elsewhere"; axios.post(url, { other: true });',
      "}",
    ].join("\n");
    const sites = callSites(route, [{ file: "fixture.js", text }]);
    expect(sites.map((s) => [s.verb, s.keys, s.how])).toEqual([
      [null, null, "not an axios call"],
    ]);
  });

  it("leaves unreadable second arguments and DELETE configs unresolved", () => {
    const helpers = ["sweep.target"];
    for (const method of ["post", "delete"]) {
      const route = { method, path: "/sweep/", helpers, definition: null };
      for (const body of ["makeBody()", "unknown", "undefined", "null"]) {
        for (const text of [
          `axios.${method}(endpoints.sweep.target(id), ${body});`,
          `const url = endpoints.sweep.target(id); axios.${method}(url, ${body});`,
        ]) {
          const sites = callSites(route, [{ file: "fixture.js", text }]);
          expect(sites.map((s) => [s.keys, s.open, s.how])).toEqual([
            [null, true, "unresolved"],
          ]);
        }
      }
      if (method === "delete") {
        const text = "axios.delete(endpoints.sweep.target(id), { headers });";
        expect(callSites(route, [{ file: "fixture.js", text }])[0].how).toBe(
          "unresolved",
        );
      }
    }
  });

  it("keeps tests and nested arguments out of URL branches", () => {
    const helpers = ["sweep.target"];
    const route = {
      method: "post",
      path: "/sweep/",
      helpers,
      definition: null,
    };
    for (const text of [
      "axios.get(endpoints.sweep.target(id) ? left : right);",
      "axios.get(wrap(endpoints.sweep.target(id)));",
      "axios.get(other, endpoints.sweep.target(id));",
    ]) {
      const sites = callSites(route, [{ file: "fixture.js", text }]);
      expect(sites.map((s) => [s.verb, s.keys, s.how])).toEqual([
        [null, null, "not an axios call"],
      ]);
    }
    const text = "axios.get(wrap(axios.post(endpoints.sweep.target(id), {})));";
    expect(
      callSites(route, [{ file: "fixture.js", text }]).map((s) => [
        s.verb,
        s.keys,
      ]),
    ).toEqual([["post", []]]);
  });

  it("counts a `payload.key =` write only for the object it is made on", () => {
    // Two handlers of one form, each building its own `payload`.
    const text = [
      "const editRole = (form) => {",
      "  const payload = { user_id: form.id };",
      "  payload.org_level = form.level;",
      "  updateRole(payload);",
      "};",
      "const resendInvite = (form) => {",
      "  const payload = { invite_id: form.id };",
      "  payload.send_email = false;",
      "  resend(payload);",
      "};",
    ].join("\n");
    const keysAt = (call) => bodyOf(text, "payload", text.indexOf(call)).keys;
    expect(keysAt("updateRole(")).toEqual(["user_id", "org_level"]);
    expect(keysAt("resend(")).toEqual(["invite_id", "send_email"]);
  });

  it("counts a FormData append only for the FormData it is made on", () => {
    const text = [
      "const upload = (file) => {",
      "  const form = new FormData();",
      '  form.append("file", file);',
      "  send(form);",
      "};",
      "const importRows = (rows) => {",
      "  const form = new FormData();",
      '  form.append("rows", rows);',
      "  post(form);",
      "};",
    ].join("\n");
    const keysAt = (call) => bodyOf(text, "form", text.indexOf(call)).keys;
    expect(keysAt("send(")).toEqual(["file"]);
    expect(keysAt("post(")).toEqual(["rows"]);
  });

  it("does not borrow a local body for a later body parameter", () => {
    const text = [
      "const bulkBody = () => {",
      '  const body = { endpoint: "hook", status: "failed" };',
      '  body.subject = "prompt:one";',
      "  return body;",
      "};",
      'const create = (body) => axios.post("/hooks", body);',
      'create({ name: "created" });',
      "const usePreview = () => useMutation({",
      '  mutationFn: (body) => axios.post("/preview", body),',
      "});",
    ].join("\n");
    expect(bodyOf(text, "body", text.indexOf("return body;"))).toEqual({
      keys: ["endpoint", "status", "subject"],
      open: false,
      how: "const",
    });
    expect(bodyOf(text, "body", text.indexOf('axios.post("/hooks"'))).toEqual({
      keys: ["name"],
      open: false,
      how: "callers(1)",
    });
    expect(
      bodyOf(text, "body", text.indexOf('axios.post("/preview"')),
    ).toBeNull();
  });

  it("reads the visible body instead of a preceding sibling's local body", () => {
    const text = [
      'const body = { name: "outer" };',
      "const sibling = () => {",
      '  const body = { endpoint: "hook" };',
      '  body.status = "failed";',
      "};",
      "body.enabled = true;",
      'axios.post("/hooks", body);',
    ].join("\n");
    expect(bodyOf(text, "body", text.indexOf("axios.post("))).toEqual({
      keys: ["name", "enabled"],
      open: false,
      how: "const",
    });
  });

  it("resolves a body initializer's spreads in the declaration's scope", () => {
    const text = [
      'const fields = { name: "outer" };',
      "const body = { ...fields };",
      "const send = () => {",
      '  const fields = { endpoint: "hook" };',
      '  axios.post("/hooks", body);',
      "};",
    ].join("\n");
    expect(bodyOf(text, "body", text.indexOf("axios.post("))).toEqual({
      keys: ["name"],
      open: false,
      how: "const",
    });
  });

  it("no call site sends a key dev refuses, outside hidden surfaces", () => {
    expect(refusedByFile()).toEqual({
      ...HIDDEN_REFUSALS,
      ...GUARDED_IN_REAL_BUILD,
      ...OTHER_LANE_REFUSALS,
      ...DEV_SENDS_TOO,
      ...ARGUMENT_IS_NOT_THE_WIRE_BODY,
    });
  });

  it("keeps the Instant eval call on its strict template route", () => {
    const path = "/model-hub/eval-templates/create-v2/";
    const route = ROUTES.find((r) => r.method === "post" && r.path === path);
    expect(route).toMatchObject({ definition: "EvalTemplateCreateV2Request" });
    expect(route.helpers).toContain("eval.createEvalTemplateV2");
    const sites = SITES.filter(
      (s) =>
        s.file ===
          "sections/projects/LLMTracing/InstantEval/useInstantEval.js" &&
        s.route === `post ${path}`,
    );
    expect(sites).toHaveLength(1);
    expect(sites[0]).toMatchObject({
      verb: "post",
      how: "buildTemplatePayload(literal)",
      open: true,
    });
    // This is still the parser's input-literal false positive, not lost coverage.
    expect([...sites[0].refused].sort()).toEqual(["now", "reads", "unit"]);
  });

  it("sends only declared keys from the Instant eval template builder", () => {
    const definition = bodyDefinitionOf(
      "post",
      "/model-hub/eval-templates/create-v2/",
    );
    const accepted = acceptedFields(definition);
    expect(accepted.length).toBeGreaterThan(0);
    [ANSWER_TYPES.YES_NO, ANSWER_TYPES.CATEGORY].forEach((answerType) => {
      const body = buildTemplatePayload({
        question: "Is the customer frustrated?",
        answerType,
        categories: ["Refund", "Shipping"],
        reads: "input_output",
        unit: "traces",
        now: new Date("2026-10-15T04:30:00Z"),
      });
      expect(Object.keys(body).filter((k) => !accepted.includes(k))).toEqual(
        [],
      );
      const path = "/model-hub/eval-templates/create-v2/";
      expect(strictViolations("post", path, body)).toEqual([]);
      ["now", "reads", "unit"].forEach((key) => {
        expect(
          strictViolations("post", path, { ...body, [key]: "unexpected" }),
        ).toEqual([key]);
      });
    });
  });

  it("keeps Summary read-only instead of sending the retired rescore body", () => {
    const file =
      "sections/experiment-detail/ExperimentSummary/Analysis/ExperimentAnalysisView.jsx";
    const view = FILES.find((f) => f.file === file);
    expect(view).toBeDefined();
    expect(view.text).toMatch(
      /import\s*\{\s*useRunAnalysis\s*\}\s*from\s*["']src\/api\/develop\/analysis["']/,
    );
    expect(view.text).toMatch(/useRunAnalysis\(datasetId,\s*\{/);
    expect(view.text).not.toMatch(
      /\b(?:axios|endpoints|useRescore|reRunExperimentCell)\b/,
    );
    expect(view.text).toMatch(/<Button\s+disabled\s*>\s*Re-score\s*<\/Button>/);
    expect(SITES.filter((s) => s.file === file)).toEqual([]);

    const path = "/model-hub/datasets/{dataset_id}/results/analysis/";
    const sites = callSites({
      method: "get",
      path,
      helpers: helpersOf(path),
      definition: null,
    }).filter((s) => s.file === "api/develop/analysis.js");
    expect(sites).toHaveLength(1);
    expect(sites[0]).toMatchObject({
      verb: "get",
      helper: "datasetRuns.analysis",
    });

    // Retiring this UI action must not make its old mutation accept these keys.
    const retired = ROUTES.find(
      (r) =>
        r.method === "post" &&
        r.helpers.includes("experiment.reRunExperimentCell"),
    );
    expect(retired).toBeDefined();
    expect(
      strictViolations("post", retired.path, {
        run_ids: ["774cc2c0-5a55-4a57-bbe6-b3d28719aa05"],
        eval_versions: { evaluator: "v2" },
      }),
    ).toEqual(["eval_versions", "run_ids"]);
  });

  it("rejects retired rescore keys on the contracted Summary analysis route", () => {
    const path = "/model-hub/datasets/{dataset_id}/results/analysis/";
    const id = "774cc2c0-5a55-4a57-bbe6-b3d28719aa05";
    const url = path.replace("{dataset_id}", id);
    const endpoint = findOpenApiEndpoint(url, "get");
    expect(endpoint).toMatchObject({
      template: path,
      method: "get",
      contract: {
        operationId: "dataset_results_analysis",
        runtimeRequestValidation: true,
      },
    });
    const params = { runs: id, baseline: "none", include: "root_causes" };
    const accepted = validateContractedRequestConfig({
      method: "get",
      url,
      params,
    });
    expect(accepted).toMatchObject({ ok: true, endpoint: { template: path } });
    expect(accepted.skipped).not.toBe(true);
    ["eval_versions", "run_ids", "unexpected"].forEach((key) => {
      const rejected = validateContractedRequestConfig({
        method: "get",
        url,
        params: { ...params, [key]: "unexpected" },
      });
      expect(rejected.ok).toBe(false);
      expect(rejected.error.details).toMatchObject({
        kind: "query",
        endpoint: path,
        method: "get",
      });
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

  it.each([RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY])(
    "%s accepts scope only when a real rerun choice is made",
    (definition) => {
      const routes = ROUTES.filter((route) => route.definition === definition);
      expect(routes.length).toBeGreaterThan(0);
      expect(acceptedFields(definition)).toContain("scope");
      const body = Object.freeze({ config: {} });

      for (const scope of ["all", "changed", "failed"]) {
        const request = withRerunScope(body, scope, definition);
        expect(request).toEqual({ ...body, scope });
        expect(body).not.toHaveProperty("scope");
        for (const route of routes) {
          expect(
            strictViolations(route.method, route.path, request),
            route.path,
          ).toEqual([]);
        }
      }

      for (const scope of [undefined, null, ""]) {
        const request = withRerunScope(body, scope, definition);
        expect(request).toBe(body);
        expect(request).not.toHaveProperty("scope");
        for (const route of routes) {
          expect(
            strictViolations(route.method, route.path, request),
            route.path,
          ).toEqual([]);
        }
      }
    },
  );

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
