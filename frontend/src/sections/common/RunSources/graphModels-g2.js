// L7c g2 (picker:search): the models an Agent graph's versions call, so a
// step-1 search for a model (`gpt-4o`) finds the Agent graph card too. The
// graph list carries no models; each graph's versions and each version's
// nodes do (GET /agent-playground/graphs/:id/versions/ and
// /versions/:versionId/, the routes the Agent graph card itself reads).
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// A search reads at most this many graphs and versions per graph.
export const MAX_GRAPHS = 20;
export const MAX_VERSIONS = 10;

/** The distinct models of a version detail's nodes, in node order (pure). */
export function modelsOfVersion(detail) {
  const out = [];
  (detail?.nodes ?? []).forEach((n) => {
    const m = String(n?.prompt_template?.model ?? n?.model ?? "").trim();
    if (m && !out.includes(m)) out.push(m);
  });
  return out;
}

/** `[{name, models}]`: each graph with its versions' models (pure merge). */
export function withGraphModels(graphs, modelsById) {
  return (graphs ?? []).map((g) => ({
    ...g,
    models: modelsById?.[g?.id] ?? [],
  }));
}

const resultOf = (res) => res?.data?.result ?? res?.data ?? null;

async function modelsOfGraph(graphId) {
  const list = resultOf(
    await axios.get(endpoints.agentPlayground.graphVersions(graphId), {
      params: { page_number: 1 },
    }),
  );
  const versions = (list?.versions ?? list?.results ?? []).slice(
    0,
    MAX_VERSIONS,
  );
  const details = await Promise.all(
    versions.map((v) =>
      axios
        .get(endpoints.agentPlayground.versionDetail(graphId, v.id))
        .then(resultOf)
        .catch(() => null),
    ),
  );
  const out = [];
  details.forEach((d) =>
    modelsOfVersion(d).forEach((m) => {
      if (!out.includes(m)) out.push(m);
    }),
  );
  return out;
}

/** `{data: {[graphId]: string[]}, isLoading}` for the graphs a search lists. */
export function useGraphModels(graphs, enabled) {
  const ids = (graphs ?? [])
    .map((g) => g?.id)
    .filter(Boolean)
    .slice(0, MAX_GRAPHS);
  return useQuery({
    queryKey: ["step1-graph-models", ids],
    enabled: Boolean(enabled) && ids.length > 0,
    staleTime: 60_000,
    queryFn: async () => {
      const pairs = await Promise.all(
        ids.map((id) =>
          modelsOfGraph(id)
            .then((models) => [id, models])
            .catch(() => [id, []]),
        ),
      );
      return Object.fromEntries(pairs);
    },
  });
}
