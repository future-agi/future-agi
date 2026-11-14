import { useCallback, useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import { ANALYTICS_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

export const SMART_AI_FILTER_TIMEOUT_MS = ANALYTICS_REQUEST_TIMEOUT_MS;

const aiFilterTimeoutError = () => {
  const error = new Error("AI filter request timed out. Please retry.");
  error.code = "ai_filter_timeout";
  return error;
};

const requireArrayResult = (data, key) => {
  const value = data?.result?.[key];
  if (!Array.isArray(value)) {
    throw new Error(`AI filter response omitted ${key}.`);
  }
  return value;
};

const schemaPropertyId = (fieldSchema) =>
  fieldSchema?.property_id ||
  fieldSchema?.propertyId ||
  fieldSchema?.registryId ||
  fieldSchema?.field ||
  "";

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
    async (
      query,
      {
        fetchValuesForFields,
        smart,
        projectId,
        source,
        agentDefinitionId,
        runTestId,
        testExecutionId,
      } = {},
    ) => {
      if (!query?.trim()) return [];

      setLoading(true);
      setError(null);

      const startedAt = Date.now();
      const controller = new AbortController();
      let rejectDeadline;
      const deadlinePromise = new Promise((_, reject) => {
        rejectDeadline = reject;
      });
      const deadlineTimer = setTimeout(() => {
        rejectDeadline(aiFilterTimeoutError());
        controller.abort();
      }, SMART_AI_FILTER_TIMEOUT_MS);

      const remainingMs = () => {
        const remaining = SMART_AI_FILTER_TIMEOUT_MS - (Date.now() - startedAt);
        if (remaining <= 0 || controller.signal.aborted) {
          throw aiFilterTimeoutError();
        }
        return Math.max(1, remaining);
      };
      const withinAction = (operation) =>
        Promise.race([Promise.resolve().then(operation), deadlinePromise]);
      const callBoundedBackend = (payload) =>
        withinAction(() =>
          callBackend(payload, {
            signal: controller.signal,
            timeout: remainingMs(),
          }),
        );

      try {
        const trimmed = query.trim();

        // Smart flow: backend runs an agentic tool-use loop. One round trip.
        // A requested smart flow must stay smart. Falling back to the legacy
        // parser would turn an unavailable/incomplete vocabulary into an
        // ungrounded literal while still looking like an AI-grounded result.
        if (smart) {
          const resolvedSource = source || "traces";
          const hasSimulationScope = Boolean(
            agentDefinitionId || runTestId || testExecutionId,
          );
          if (resolvedSource === "simulation" && !hasSimulationScope) {
            throw new Error(
              "Select an agent, simulation, or test execution before using AI value grounding.",
            );
          }
          if (resolvedSource !== "simulation" && !projectId) {
            throw new Error(
              "Select a project before using AI value grounding.",
            );
          }
          const data = await callBoundedBackend({
            query: trimmed,
            schema,
            mode: "smart",
            ...(projectId ? { project_id: projectId } : {}),
            ...(agentDefinitionId
              ? { agent_definition_id: agentDefinitionId }
              : {}),
            ...(runTestId ? { run_test_id: runTestId } : {}),
            ...(testExecutionId ? { test_execution_id: testExecutionId } : {}),
            source: resolvedSource,
          });
          return requireArrayResult(data, "filters");
        }

        // Multi-step flow: only when the caller wired up value fetching.
        if (typeof fetchValuesForFields === "function") {
          // Step 1 — ask which fields are relevant. Strip operators/type
          // from the schema so the LLM gets a compact payload; we only
          // need field id + human label + category to pick.
          const compactSchema = schema.map((fieldSchema) => ({
            field: fieldSchema.field,
            property_id: schemaPropertyId(fieldSchema),
            label: fieldSchema.label,
            category: fieldSchema.category,
          }));
          const selectData = await callBoundedBackend({
            query: trimmed,
            schema: compactSchema,
            mode: "select_fields",
          });
          const picked = requireArrayResult(selectData, "fields");

          if (!picked.length) {
            // Fall through to a plain build with the base schema so the LLM
            // still has a chance to produce a filter (e.g. string "contains").
            const data = await callBoundedBackend({ query: trimmed, schema });
            return requireArrayResult(data, "filters");
          }

          // Step 2 — fetch real values for the picked fields.
          const valuesByField =
            (await withinAction(() =>
              fetchValuesForFields(picked, {
                signal: controller.signal,
                timeoutMs: remainingMs(),
              }),
            )) || {};
          if (
            typeof valuesByField !== "object" ||
            Array.isArray(valuesByField)
          ) {
            throw new Error(
              "AI filter value lookup returned an invalid result.",
            );
          }

          // Build a reduced schema limited to the picked fields, enriched
          // with real choices where available. Fields without any fetched
          // values still go through so free-text filters can be produced.
          const enrichedSchema = schema
            .filter((s) => picked.includes(schemaPropertyId(s)))
            .map((s) => {
              const propertyId = schemaPropertyId(s);
              const vals = valuesByField[propertyId] ?? valuesByField[s.field];
              if (Array.isArray(vals) && vals.length > 0) {
                return { ...s, choices: vals.slice(0, 200) };
              }
              return s;
            });

          // Step 3 — build the final filter with value-aware schema.
          const data = await callBoundedBackend({
            query: trimmed,
            schema: enrichedSchema,
            mode: "build_filters",
          });
          return requireArrayResult(data, "filters");
        }

        // Single-step fallback.
        const data = await callBoundedBackend({ query: trimmed, schema });
        return requireArrayResult(data, "filters");
      } catch (err) {
        const message =
          err?.response?.data?.result || err?.message || "AI filter failed";
        setError(
          typeof message === "string" ? message : JSON.stringify(message),
        );
        throw err;
      } finally {
        clearTimeout(deadlineTimer);
        controller.abort();
        setLoading(false);
      }
    },
    [schema, callBackend],
  );

  return { parseQuery, loading, error };
}
