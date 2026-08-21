import { useCallback, useState } from "react";
import axios, { endpoints } from "src/utils/axios";

export const SMART_AI_FILTER_TIMEOUT_MS = 9500;

/**
 * Reusable AI filter hook.
 *
 * Three modes:
 *
 * 1. Smart (recommended for trace filtering): the backend runs an agentic
 *    tool-use loop where Haiku autonomously fetches real field values via
 *    a `get_field_values` tool before picking a value. One HTTP call.
 *    Caller must provide `projectId` and `source` so the backend can
 *    query ClickHouse on its behalf.
 *
 *      const filters = await parseQuery(query, {
 *        smart: true,
 *        projectId: observeId,
 *        source: "traces",
 *      });
 *
 * 2. Multi-step (legacy): when a `fetchValuesForFields` callback is
 *    provided, the hook orchestrates a 3-step flow:
 *      step 1 — backend picks relevant field ids
 *      step 2 — caller fetches real values
 *      step 3 — backend builds the final filter with the values inlined
 *    Use this when the caller wants client-side control over value
 *    fetching (e.g. fetching from a non-CH source).
 *
 * 3. Single-step (default, used by evals): one backend round-trip with
 *    a static schema. The LLM is constrained by `choices` per field if
 *    the caller supplies them.
 */
export function useAIFilter(schema) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const callBackend = useCallback(async (payload, config) => {
    const { data } = await axios.post(
      endpoints.develop.eval.aiFilter,
      payload,
      config,
    );
    return data;
  }, []);

  const parseQuery = useCallback(
    async (query, { fetchValuesForFields, smart, projectId, source } = {}) => {
      if (!query?.trim()) return [];

      setLoading(true);
      setError(null);

      try {
        const trimmed = query.trim();

        // Smart flow: backend runs an agentic tool-use loop. One round trip.
        // A requested smart flow must stay smart. Falling back to the legacy
        // parser would turn an unavailable/incomplete vocabulary into an
        // ungrounded literal while still looking like an AI-grounded result.
        if (smart) {
          if (!projectId) {
            throw new Error(
              "Select a project before using AI value grounding.",
            );
          }
          const data = await callBackend(
            {
              query: trimmed,
              schema,
              mode: "smart",
              project_id: projectId,
              source: source || "traces",
            },
            { timeout: SMART_AI_FILTER_TIMEOUT_MS },
          );
          return data?.result?.filters || [];
        }

        // Multi-step flow: only when the caller wired up value fetching.
        if (typeof fetchValuesForFields === "function") {
          // Step 1 — ask which fields are relevant. Strip operators/type
          // from the schema so the LLM gets a compact payload; we only
          // need field id + human label + category to pick.
          const compactSchema = schema.map(({ field, label, category }) => ({
            field,
            label,
            category,
          }));
          const selectData = await callBackend({
            query: trimmed,
            schema: compactSchema,
            mode: "select_fields",
          });
          const picked = selectData?.result?.fields || [];

          if (!picked.length) {
            // Fall through to a plain build with the base schema so the LLM
            // still has a chance to produce a filter (e.g. string "contains").
            const data = await callBackend({ query: trimmed, schema });
            return data?.result?.filters || [];
          }

          // Step 2 — fetch real values for the picked fields.
          let valuesByField = {};
          try {
            valuesByField = (await fetchValuesForFields(picked)) || {};
          } catch (fetchErr) {
            valuesByField = {};
          }

          // Build a reduced schema limited to the picked fields, enriched
          // with real choices where available. Fields without any fetched
          // values still go through so free-text filters can be produced.
          const enrichedSchema = schema
            .filter((s) => picked.includes(s.field))
            .map((s) => {
              const vals = valuesByField[s.field];
              if (Array.isArray(vals) && vals.length > 0) {
                return { ...s, choices: vals.slice(0, 200) };
              }
              return s;
            });

          // Step 3 — build the final filter with value-aware schema.
          const data = await callBackend({
            query: trimmed,
            schema: enrichedSchema,
            mode: "build_filters",
          });
          return data?.result?.filters || [];
        }

        // Single-step fallback.
        const data = await callBackend({ query: trimmed, schema });
        return data?.result?.filters || [];
      } catch (err) {
        const message =
          err?.response?.data?.result || err?.message || "AI filter failed";
        setError(
          typeof message === "string" ? message : JSON.stringify(message),
        );
        if (smart) throw err;
        return [];
      } finally {
        setLoading(false);
      }
    },
    [schema, callBackend],
  );

  return { parseQuery, loading, error };
}
