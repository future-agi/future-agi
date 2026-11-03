import {
  TracerObservationSpanListSpansObserveResponse,
  TracerTraceListTracesOfSessionResponse,
  TracerTraceSessionListSessionsResponse,
} from "src/generated/api-contracts/api.zod";
import { getSpanPhysicalRowId } from "src/sections/projects/LLMTracing/spanPhysicalIdentity";

export const traceSelectorRowIdentity = (row) => row?.trace_id ?? null;

export const spanSelectorRowIdentity = getSpanPhysicalRowId;

export const sessionSelectorRowIdentity = (row) => row?.session_id ?? null;

const parseSelectorPage = ({ response, schema, sourceLabel, rowIdentity }) => {
  const parsed = schema.parse(response?.data);
  if (parsed.status !== true) {
    throw new Error(`${sourceLabel} list response was not successful`);
  }
  parsed.result.table.forEach((row, index) => {
    const identity = rowIdentity(row);
    if (typeof identity !== "string" || identity.length === 0) {
      throw new Error(
        `${sourceLabel} list row #${index} is missing its canonical identity`,
      );
    }
  });
  return parsed.result;
};

export const parseTraceSelectorPage = (response) =>
  parseSelectorPage({
    response,
    schema: TracerTraceListTracesOfSessionResponse,
    sourceLabel: "Trace",
    rowIdentity: traceSelectorRowIdentity,
  });

export const parseSpanSelectorPage = (response) =>
  parseSelectorPage({
    response,
    schema: TracerObservationSpanListSpansObserveResponse,
    sourceLabel: "Span",
    rowIdentity: spanSelectorRowIdentity,
  });

export const parseSessionSelectorPage = (response) =>
  parseSelectorPage({
    response,
    schema: TracerTraceSessionListSessionsResponse,
    sourceLabel: "Session",
    rowIdentity: sessionSelectorRowIdentity,
  });

export const traceSelectorRowsFromResponse = (response) =>
  parseTraceSelectorPage(response).table;

export const traceSelectorMetadataFromResponse = (response) =>
  parseTraceSelectorPage(response).metadata;

export const spanSelectorRowsFromResponse = (response) =>
  parseSpanSelectorPage(response).table;

export const spanSelectorMetadataFromResponse = (response) =>
  parseSpanSelectorPage(response).metadata;

export const sessionSelectorRowsFromResponse = (response) =>
  parseSessionSelectorPage(response).table;

export const sessionSelectorMetadataFromResponse = (response) =>
  parseSessionSelectorPage(response).metadata;
