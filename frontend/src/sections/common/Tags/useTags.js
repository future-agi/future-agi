// W5-3 (W5-SCOPE §7 C1): the tag registry and experiment tags over the API.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

export const TAGS_KEY = ["w5", "tags"];

/** The registry; `experiments` = experiment_id -> tag ids. */
export function useTags({ enabled = true } = {}) {
  return useQuery({
    queryKey: TAGS_KEY,
    queryFn: () =>
      axios.get(endpoints.develop.w5.tags, {
        params: { include: "experiments" },
      }),
    select: (res) => ({
      tags: res?.data?.result?.results ?? [],
      experiments: res?.data?.result?.experiments ?? {},
    }),
    enabled,
    staleTime: 5000,
  });
}

const errorText = (e) =>
  e?.response?.data?.result ?? e?.result ?? e?.message ?? "Something failed.";

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: TAGS_KEY });
}

export function useCreateTag() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (body) => axios.post(endpoints.develop.w5.tags, body),
    onSuccess: done,
  });
}

export function useUpdateTag() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...patch }) =>
      axios.patch(endpoints.develop.w5.tag(id), patch),
    onSuccess: done,
  });
}

export function useDeleteTag() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.develop.w5.tag(id)),
    onSuccess: done,
  });
}

/** PUT one experiment's tag ids; `mutateAsync` for several in a row. */
export function useSetExperimentTags() {
  const done = useInvalidate();
  return useMutation({
    mutationFn: ({ experimentId, tagIds }) =>
      axios.put(endpoints.develop.w5.experimentTags(experimentId), {
        tag_ids: tagIds,
      }),
    onSuccess: done,
  });
}

/**
 * One experiment's tags as a draft (Edit experiment): `ids` starts as the
 * saved list; `save()` PUTs only when it changed.
 * @returns {{ids: string[], setIds: Function, dirty: boolean, tags: object[],
 *   save: () => Promise<void>, saving: boolean, error: string}}
 */
export function useExperimentTagsDraft(experimentId, { enabled = true } = {}) {
  const { data } = useTags({ enabled: enabled && Boolean(experimentId) });
  const saved = useMemo(
    () => data?.experiments?.[experimentId] ?? [],
    [data?.experiments, experimentId],
  );
  const [ids, setIds] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setIds(null);
    setError("");
  }, [experimentId]);
  const put = useSetExperimentTags();
  const current = ids ?? saved;
  const dirty =
    ids != null &&
    (ids.length !== saved.length || ids.some((id) => !saved.includes(id)));
  const save = async () => {
    if (!dirty) return;
    setError("");
    try {
      await put.mutateAsync({ experimentId, tagIds: ids });
    } catch (e) {
      setError(String(errorText(e)));
      throw e;
    }
  };
  return {
    ids: current,
    setIds,
    dirty,
    tags: data?.tags ?? [],
    save,
    saving: put.isPending,
    error,
  };
}

/**
 * Tagging the experiments of selected runs: `lists` (each experiment's tag
 * ids, for TagPicker `itemTagIds`), `onChange` (one PUT per experiment),
 * `title` and `n` (experiments).
 */
export function useExperimentTagging(runs) {
  const { data } = useTags();
  const setTags = useSetExperimentTags();
  const experimentIds = useMemo(
    () => [
      ...new Set((runs ?? []).map((r) => r.experiment_id).filter(Boolean)),
    ],
    [runs],
  );
  const lists = experimentIds.map((id) => data?.experiments?.[id] ?? []);
  const n = experimentIds.length;
  const onChange = async (nextLists) => {
    try {
      await Promise.all(
        experimentIds.map((experimentId, i) =>
          setTags.mutateAsync({ experimentId, tagIds: nextLists[i] }),
        ),
      );
    } catch (e) {
      enqueueSnackbar(String(errorText(e)), { variant: "error" });
    }
  };
  return {
    lists,
    onChange,
    n,
    title: `Tags on ${n} experiment${n === 1 ? "" : "s"}`,
  };
}

export { errorText as tagErrorText };
