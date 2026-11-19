import { useMutation, useQuery } from "@tanstack/react-query";
import { readAsOf } from "src/sections/simulate/environments/buildDrawer/forms/shared/buildFiles";
import { useDesignDraftStore } from "src/sections/simulate/environments/design/useDesignDraftStore";

// Stub of the design job's build after approval (#3612): approve starts it,
// then it's polled for its stages, validation and release. Nothing is sent;
// see UI_ONLY_FEATURES.designBuild. The draft keeps when it was approved, so
// a reload finds the build where it was.
export const STAGE_MS = 1500;
export const VALIDATE_MS = 2000;
const POLL_MS = 500;

export const BUILD_STAGES = [
  { id: "records", label: "Records and data" },
  { id: "tools", label: "Tool handlers" },
  { id: "checks", label: "Rules and checks" },
  { id: "scenarios", label: "Scenarios" },
];
const BUILD_MS = BUILD_STAGES.length * STAGE_MS;

const CHECKS = [
  "Records load and every key points at a record",
  "Each tool runs against the seeded records",
  "Each rule has a check",
  "Each scenario can start",
];

export const FINAL_BUILD = ["saved", "needs_repair"];

/**
 * The build of a draft at `now`, or null before approval: { status:
 * building | validating | saved | needs_repair, stages: [{ id, label,
 * status: queued | running | done }], validation: { checks: [{ label, ok,
 * detail, file? }] } once checked, release: { version } once saved }. A
 * records file with "broken" in its name fails the key check, naming the file.
 */
export function designBuildOf(draft, now = Date.now()) {
  const startedAt = draft?.build?.startedAt;
  if (!startedAt) return null;
  const t = now - startedAt;
  const at = Math.floor(t / STAGE_MS);
  const stages = BUILD_STAGES.map((s, i) => ({
    ...s,
    status: i < at ? "done" : i === at ? "running" : "queued",
  }));
  if (t < BUILD_MS) return { status: "building", stages, validation: null };
  if (t < BUILD_MS + VALIDATE_MS)
    return { status: "validating", stages, validation: null };
  const broken = (draft.files || []).find(
    (f) => /broken/i.test(f.name) && readAsOf(f.name) === "Records",
  );
  const checks = CHECKS.map((label, i) =>
    i === 0 && broken
      ? {
          label,
          ok: false,
          detail: `Some rows in ${broken.name} point at records that aren't there.`,
          file: { id: broken.id, name: broken.name },
        }
      : { label, ok: true, detail: null },
  );
  return broken
    ? { status: "needs_repair", stages, validation: { checks } }
    : {
        status: "saved",
        stages,
        validation: { checks },
        release: { version: 1 },
      };
}

const buildKey = (draftId, startedAt) => [
  "simulate-environments",
  "design-build",
  draftId,
  startedAt,
];

/** The draft's build, polled until it's saved or needs repair; null before approval. */
export function useDesignBuild(draftId) {
  const draft = useDesignDraftStore((s) => s.drafts[draftId]);
  const startedAt = draft?.build?.startedAt;
  const { data } = useQuery({
    queryKey: buildKey(draftId, startedAt),
    queryFn: () =>
      designBuildOf(useDesignDraftStore.getState().drafts[draftId]),
    initialData: () => designBuildOf(draft),
    enabled: !!startedAt,
    refetchInterval: (query) =>
      FINAL_BUILD.includes(query.state.data?.status) ? false : POLL_MS,
  });
  return startedAt ? data : null;
}

/**
 * Approves the design and starts its build. Does nothing while a build runs
 * or once it's saved; after Needs repair it builds again.
 */
export function useApproveDesign(draftId) {
  return useMutation({
    mutationFn: async () => {
      const { drafts, startDesignBuild } = useDesignDraftStore.getState();
      const status = designBuildOf(drafts[draftId])?.status;
      if (status && status !== "needs_repair") return;
      startDesignBuild(draftId);
    },
  });
}
