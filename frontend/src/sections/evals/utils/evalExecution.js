// Shared router for the "Test eval against a tracing row" surfaces
// (TracingTestMode, TaskLivePreview). Routes between `/eval-playground/`,
// `/composite/execute/`, and `/composite/execute-adhoc/` so the
// composite-vs-single decision lives in one place.

import axios, { endpoints } from "src/utils/axios";

import { resolvePath } from "./rowPathWalker";
import { buildCompositeRuntimeConfig } from "../Helpers/compositeRuntimeConfig";

export const normalizeRowType = (value) => {
  if (!value) return "Span";
  const v = String(value).toLowerCase();
  if (v === "span" || v === "spans") return "Span";
  if (v === "trace" || v === "traces") return "Trace";
  if (v === "session" || v === "sessions") return "Session";
  if (
    v === "voicecall" ||
    v === "voicecalls" ||
    v === "voice_calls" ||
    v === "voice"
  ) {
    return "VoiceCall";
  }
  return "Span";
};

// Single-eval ctx: just IDs — the BE resolves {{span}}/{{trace}}/{{session}}.
export const buildAutoCtx = ({ rowType, currentRow }) => {
  const ctx = {};
  if (!currentRow) return ctx;
  const t = normalizeRowType(rowType);
  const spanId = currentRow.span_id || currentRow.spanId;
  const traceId = currentRow.trace_id || currentRow.traceId;
  const sessionId = currentRow.session_id || currentRow.sessionId;
  if (t === "Span" && spanId) ctx.span_id = spanId;
  if ((t === "Span" || t === "Trace") && traceId) ctx.trace_id = traceId;
  if (t === "Session" && sessionId) ctx.session_id = sessionId;
  if (t === "VoiceCall" && traceId) ctx.trace_id = traceId;
  return ctx;
};

// Composite ctx: full objects — the composite endpoint doesn't resolve IDs.
export const buildCompositeCtx = ({ rowType, currentRow, spanDetail }) => {
  const ctx = {};
  const t = normalizeRowType(rowType);
  if (t === "Span" && spanDetail) ctx.span_context = spanDetail;
  if (t === "Trace" && currentRow) ctx.trace_context = currentRow;
  if (t === "Session" && currentRow) ctx.session_context = currentRow;
  if (t === "VoiceCall" && currentRow) ctx.trace_context = currentRow;
  return ctx;
};

// `rowFields` is an optional fallback for fields not present in spanDetail
// (e.g. annotation columns in the TracingTestMode mapping panel).
export const resolveMappingFromRow = (mapping, spanDetail, rowFields) => {
  const resolved = {};
  for (const [variable, field] of Object.entries(mapping || {})) {
    if (!field) continue;
    const hit = resolvePath(spanDetail, field);
    let val = hit.status === "resolved" ? hit.value : undefined;
    if (val === undefined && Array.isArray(rowFields)) {
      const rf = rowFields.find((f) => f.key === field || f.colId === field);
      if (rf?.raw !== undefined && rf.raw !== null) {
        val = rf.raw;
      }
    }
    if (val !== undefined && val !== null) {
      resolved[variable] =
        typeof val === "object" ? JSON.stringify(val) : String(val);
    }
  }
  return resolved;
};

// Returns: { ok, isComposite, output, reason, compositeResult, logId, raw, errorMessage }
//
// `singleEvalConfigExtras` / `compositeConfigExtras` are merged into the
// outgoing `config` — callers use them to forward saved `run_config` flags
// (TaskLivePreview) or UI-form params (TracingTestMode).
export const executeEvalForRow = async ({
  evalItem,
  rowType,
  currentRow,
  spanDetail,
  mapping,
  rowFields,
  codeParams,
  errorLocalizerEnabled = false,
  compositeAdhocConfig = null,
  singleEvalConfigExtras = {},
  compositeConfigExtras = {},
}) => {
  const templateId = evalItem?.template_id ?? evalItem?.templateId;
  const model = evalItem?.model || "turing_large";
  const templateType = evalItem?.template_type ?? evalItem?.templateType;
  const isComposite =
    templateType === "composite" || !!compositeAdhocConfig;
  const t = normalizeRowType(rowType);
  const isSession = t === "Session";

  // Sessions skip the walk — single delegates via `mapping_paths`, composite
  // uses `session_context = currentRow`.
  const resolvedMapping = isSession
    ? {}
    : resolveMappingFromRow(mapping, spanDetail, rowFields);

  try {
    if (isComposite) {
      const compositeCtx = buildCompositeCtx({
        rowType: t,
        currentRow,
        spanDetail,
      });
      const compositeConfig = buildCompositeRuntimeConfig({
        config: compositeConfigExtras,
        codeParams,
      });
      const basePayload = {
        mapping: resolvedMapping,
        model,
        error_localizer: errorLocalizerEnabled,
        config: compositeConfig,
        ...compositeCtx,
      };
      const url = compositeAdhocConfig
        ? endpoints.develop.eval.executeCompositeEvalAdhoc
        : endpoints.develop.eval.executeCompositeEval(templateId);
      const payload = compositeAdhocConfig
        ? { ...compositeAdhocConfig, ...basePayload }
        : basePayload;
      const { data } = await axios.post(url, payload);
      if (!data?.status) {
        return {
          ok: false,
          isComposite: true,
          errorMessage: data?.result || "Evaluation failed",
          raw: data,
        };
      }
      const result = data.result;
      return {
        ok: true,
        isComposite: true,
        output:
          result?.aggregation_enabled && result?.aggregate_score != null
            ? result.aggregate_score
            : null,
        reason: result?.summary || "",
        compositeResult: result,
        raw: result,
      };
    }

    // Single-eval: sessions send `mapping_paths` (BE resolves against the
    // real DB); other row types send the locally-resolved `mapping`.
    const autoCtx = buildAutoCtx({ rowType: t, currentRow });
    const singleConfig = { ...singleEvalConfigExtras };
    if (!isSession) singleConfig.mapping = resolvedMapping;
    if (codeParams && Object.keys(codeParams).length > 0) {
      singleConfig.params = { ...(singleConfig.params || {}), ...codeParams };
    }
    const payload = {
      template_id: templateId,
      model,
      error_localizer: errorLocalizerEnabled,
      config: singleConfig,
      ...(isSession ? { mapping_paths: mapping || {} } : {}),
      ...autoCtx,
    };
    const { data } = await axios.post(
      endpoints.develop.eval.evalPlayground,
      payload,
    );
    if (!data?.status) {
      return {
        ok: false,
        isComposite: false,
        errorMessage: data?.result || "Evaluation failed",
        raw: data,
      };
    }
    return {
      ok: true,
      isComposite: false,
      output: data.result,
      raw: data.result,
      logId: data.result?.log_id ?? null,
    };
  } catch (err) {
    return {
      ok: false,
      isComposite,
      errorMessage:
        err?.response?.data?.result ||
        err?.result ||
        err?.detail ||
        err?.message ||
        "Failed to run evaluation",
      raw: err,
    };
  }
};
