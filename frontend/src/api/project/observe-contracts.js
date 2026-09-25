import {
  TracerObservationSpanListSpansObserveResponse,
  TracerTraceGetGraphMethodsResponse,
  TracerTraceSessionListSessionsResponse,
  TracerTraceListTracesOfSessionResponse,
  TracerTraceVoiceCallDetailResponse,
  TracerTraceListVoiceCallsResponse,
} from "src/generated/api-contracts/api.zod";
import { z } from "zod";

// The OpenAPI document correctly marks graph values nullable, but the current
// Zod generator drops `x-nullable`. Extend the generated envelope/result so we
// keep all generated metadata validation while accepting the null gaps that
// the backend and chart renderers explicitly support.
const nullableGraphPoint = z.object({
  timestamp: z.string().min(1),
  value: z.number().nullable(),
  primary_traffic: z.number().nullable().optional(),
});
// The generated schema closes `metric_statistic` to today's enum. A newer
// server may name a statistic this tab predates (for example p95); rejecting
// it would fail the whole graph until a reload. Accept any string: the label
// logic keys on "median" and falls back to the plain name otherwise.
const traceGraphResponse = TracerTraceGetGraphMethodsResponse.extend({
  result: TracerTraceGetGraphMethodsResponse.shape.result.extend({
    data: z.array(nullableGraphPoint),
    metric_statistic: z.string().optional(),
  }),
});

const successfulResult = (schema, payload, label) => {
  const response = schema.parse(payload);
  if (response.status !== true) {
    throw new Error(`${label} response was not successful`);
  }
  return response.result;
};

/** Parse one trace graph HTTP body into its sole presentation shape. */
export const parseTraceGraphResponse = (payload) =>
  successfulResult(traceGraphResponse, payload, "Trace graph");

/** Parse one Observe trace-list HTTP body into the generated result shape. */
export const parseTraceObserveListResponse = (payload) =>
  successfulResult(
    TracerTraceListTracesOfSessionResponse,
    payload,
    "Trace list",
  );

/** Parse one Observe span-list HTTP body into the generated result shape. */
export const parseSpanObserveListResponse = (payload) =>
  successfulResult(
    TracerObservationSpanListSpansObserveResponse,
    payload,
    "Span list",
  );

/** Parse one Observe session-list HTTP body into the generated result shape. */
export const parseSessionObserveListResponse = (payload) =>
  successfulResult(
    TracerTraceSessionListSessionsResponse,
    payload,
    "Session list",
  );

/** Voice-call list is the declared direct-body exception (no result wrapper). */
export const parseVoiceCallListResponse = (payload) =>
  TracerTraceListVoiceCallsResponse.parse(payload);

/** Voice-call detail is a success envelope containing one detail object. */
export const parseVoiceCallDetailResponse = (payload) =>
  successfulResult(
    TracerTraceVoiceCallDetailResponse,
    payload,
    "Voice-call detail",
  );

/** Keep transport metadata while replacing the body with its canonical result. */
export const parseAxiosResult = (response, parser) => ({
  ...response,
  data: parser(response.data),
});
