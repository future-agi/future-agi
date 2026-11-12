// W6 FE slot `cardExtras/endpoint` (W6-SCOPE.md §4.2):
// the create-body keys an endpoint card adds to its run
// entry, through `RunSettings/cardExtras.js` `cardExtrasOf(card)`
// (one spread line in experimentFormValues.js). Pure.
//   endpoint: {transform: {output?, tool_calls?, usage?}, preset?: {id, name}}
//   context_source: {variable, kb_id, top_k, query_column} (B5, any card)
import { transformOf } from "src/sections/common/RunSources/endpoint/transformModel";
import { contextSourceOf } from "src/sections/common/RunSettings/contextSourceModel";

const s = (v) => String(v ?? "").trim();

export function endpointCardExtras(card = {}) {
  const out = {};
  const http = card.http;
  if (http) {
    const t = http.transform ?? {};
    const hasTransform =
      s(t.output) || s(t.tool_calls) || Object.values(t.usage ?? {}).some(s);
    const transform = hasTransform
      ? transformOf({ output_path: http.outputPath, transform: t })
      : null;
    const preset = http.preset?.id
      ? { id: http.preset.id, name: http.preset.name }
      : null;
    if (transform || preset)
      out.endpoint = {
        ...(transform ? { transform } : {}),
        ...(preset ? { preset } : {}),
      };
  }
  const cs = contextSourceOf(card.contextSource ?? card.context_source);
  if (cs) out.context_source = cs;
  return out;
}

const slot = { extrasOf: endpointCardExtras };

export default slot;
