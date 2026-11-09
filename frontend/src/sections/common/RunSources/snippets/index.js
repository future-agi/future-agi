// The single snippet generator (lane Ra brief MUST 4): the drawer's
// Level 3 and lane Rb's examples both call snippetFor. Code is generated
// here (python.js / typescript.js); harness, CI and sandbox
// dispatch to lane Rb's slot files (stubs until Rb merges). Pure.
//
//   snippetFor({kind, entryId, mode, form, lang, ids, view}) →
//     {steps, fileName?, file?, tryCommand?, status}
import { catalogEntry } from "../catalog";
import { traceUploadSteps, traceUploadScript } from "./python";
import { traceUploadStepsTs, traceUploadScriptTs } from "./typescript";
import * as harness from "./prototypeHarness";
import * as ci from "./ci";
import * as sandbox from "./sandbox";

export { PATH_STATUS, UPLOAD_HEADER } from "./lines";

/** The language a code entry's snippets use by default. */
export function defaultLang(entry) {
  const langs = entry?.languages ?? [];
  if (langs.includes("python")) return "python";
  if (langs.includes("typescript")) return "typescript";
  return "python";
}

const slotResult = (mod, args) => {
  const steps = mod.steps(args) ?? [];
  const preview = steps.some((s) => s.status === "preview");
  return { steps, status: preview ? "preview" : "today" };
};

/**
 * @param {{kind: string, entryId?: string, mode?: string,
 *   form?: "upload"|"connect", lang?: "python"|"typescript",
 *   view?: "cli"|"code", ids?: object}} p
 */
export function snippetFor({
  kind,
  entryId,
  mode,
  form = "upload",
  lang,
  ids = {},
} = {}) {
  const entry = catalogEntry(entryId);
  const args = { entry, mode, form, lang, ids };
  if (kind === "harness") return slotResult(harness, args);
  if (mode === "ci") return slotResult(ci, args);
  if (mode === "sandbox") return slotResult(sandbox, args);
  const language = lang ?? defaultLang(entry);
  const ts = language === "typescript";
  return ts
    ? traceUploadStepsTs({ entry, ids })
    : traceUploadSteps({ entry, ids });
}

/** The complete file text alone (tests, `Copy complete script`). */
export function fileFor({ entryId, lang, ids = {} }) {
  const entry = catalogEntry(entryId);
  const ts = (lang ?? defaultLang(entry)) === "typescript";
  return ts
    ? traceUploadScriptTs({ entry, ids })
    : traceUploadScript({ entry, ids });
}
