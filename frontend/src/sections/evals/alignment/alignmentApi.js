// W7: the Agreement tab's reads and writes. Paths come from the integration
// registry block (`endpoints.develop.w7`, W7-SCOPE §3.1); until it merges
// the hooks stay disabled and the tab renders nothing.
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { customPricesOf } from "src/sections/develop-detail/Experiment/estimateInputs";
import { COPY_INSTRUCTIONS, copyNameOf } from "./editableCopy";

const w7 = () => endpoints.develop?.w7 ?? null;

export const hasAlignmentRoutes = () => Boolean(w7()?.agreement);

/**
 * GET …/agreement/ (sources + one source's report). `keepPrevious` keeps
 * the last answer on screen while a new one loads (the Agreement tab never
 * swaps a loaded report for a spinner).
 */
export function useAgreement(
  templateId,
  params = {},
  { enabled = true, keepPrevious = false } = {},
) {
  return useQuery({
    queryKey: ["w7-agreement", templateId, params],
    queryFn: () =>
      axios.get(w7().agreement(templateId), {
        params: Object.fromEntries(
          Object.entries(params).filter(([, v]) => v != null && v !== ""),
        ),
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(enabled && templateId && hasAlignmentRoutes()),
    staleTime: 30_000,
    ...(keepPrevious ? { placeholderData: keepPreviousData } : {}),
  });
}

/**
 * Re-read the agreement answers after an editable copy or a version was
 * made. A compare or a tuned-judge check already shown stays as it was
 * scored (its numbers describe the definition it ran on).
 */
export function useRefreshAgreement() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({
      queryKey: ["w7-agreement"],
      predicate: (q) => !q.queryKey?.[2]?.compare_judge_model,
    });
}

/**
 * The org's custom model prices (estimateInputs.customPricesOf of GET
 * /model-hub/custom-models/), for the confirms' estimates. `refresh()`
 * re-reads them; a confirm opens only after it, so a tuned model added in
 * Settings › Custom models a moment ago is priced at its own prices.
 * -> {customPrices, refresh}
 */
export function useCustomPrices() {
  const q = useQuery({
    queryKey: ["w7-custom-prices"],
    queryFn: () => axios.get(endpoints.model.list),
    select: (res) =>
      customPricesOf(res?.data?.results ?? res?.data?.result?.results ?? []),
    staleTime: 0,
  });
  return {
    customPrices: q.data ?? null,
    refresh: () => q.refetch().catch(() => null),
  };
}

/** GET …/agreement/?view=confidence&run_id= (decision judges). */
export function useConfidence(templateId, runId, { enabled = true } = {}) {
  return useQuery({
    queryKey: ["w7-confidence", templateId, runId ?? null],
    queryFn: () =>
      axios.get(w7().agreement(templateId), {
        params: { view: "confidence", ...(runId ? { run_id: runId } : {}) },
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: Boolean(enabled && templateId && hasAlignmentRoutes()),
  });
}

/** POST …/improve/ (candidates; with edits, the scored draft). */
export function useImprove(templateId) {
  return useMutation({
    mutationFn: (body) =>
      axios
        .post(w7().improve(templateId), body)
        .then((res) => res?.data?.result ?? null),
  });
}

/**
 * The editable copy of a reference-kind built-in (W7-SCOPE §2.6): the copy
 * made earlier, found by its name (`<name>_aligned`; names are unique), else
 * a new one through create-v2. Improve judge and Use as this eval's judge
 * share it, so a second visit never trips "already exists".
 * -> {id, name, existed}
 */
export async function editableCopyOf({ templateName, outputType, model }) {
  const name = copyNameOf(templateName);
  const { data: found } = await axios.post(
    endpoints.develop.eval.getEvalNames,
    {
      search_text: name,
    },
  );
  const list = found?.result ?? found ?? [];
  const hit = (Array.isArray(list) ? list : []).find((t) => t?.name === name);
  if (hit) return { id: hit.id, name: hit.name, existed: true };
  const { data } = await axios.post(
    endpoints.develop.eval.createEvalTemplateV2,
    {
      name,
      eval_type: "llm",
      instructions: COPY_INSTRUCTIONS,
      output_type: outputType ?? "pass_fail",
      model,
    },
  );
  return { id: data?.result?.id, name: data?.result?.name, existed: false };
}
