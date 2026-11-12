// The one host of `Rerun with changes…` (R4-SPEC D8), mounted once in the
// app shell by implementation; lane Ra's body. When a run asks
// (rerunBus.openRerunWithChanges, `shift+R`, run menus) it finds the run
// on the dataset in view and opens RerunWithChangesDialog.
import React, { Suspense, lazy, useEffect, useMemo } from "react";
import { useLocation } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useDatasetRuns } from "src/api/develop/runs";
import { useGetExperimentDetails } from "src/hooks/useGetExperimentDetails";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { openRerunWithChanges, useRerunRequest } from "./rerunBus";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { isPrototypeMode } from "src/utils/prototype";
// GS-06 (options-truth L3 G10): the host is in the app shell on every page;
// the open dialog (its matrix read, estimates and code blocks) loads only
// when a rerun is asked for.
const RerunWithChangesOpen = lazy(() => import("./RerunWithChangesOpen"));

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

/** Dataset / experiment ids of a dashboard path (pure). */
export function routeIdsOf(pathname) {
  const p = String(pathname ?? "");
  const exp = p.match(
    new RegExp(`/dashboard/develop/experiment/(${UUID})`, "i"),
  );
  if (exp) return { experimentId: exp[1], datasetId: null };
  const ds = p.match(new RegExp(`/dashboard/develop/(${UUID})`, "i"));
  return { experimentId: null, datasetId: ds?.[1] ?? null };
}

export default function RerunWithChangesHost() {
  const { request, close } = useRerunRequest();
  const location = useLocation();
  // L6 launch switch (exp.rerun): where the dialog does not ship, the host
  // reads nothing (no experiment, no runs), whoever mounts it.
  // The real classic page owns its v2 append dialog and needs no run-list or matrix reads.
  const shown = isPrototypeMode() && isSurfaceShown("exp.rerun");
  const ids = shown
    ? routeIdsOf(location.pathname)
    : { experimentId: null, datasetId: null };
  const { data: exp } = useGetExperimentDetails(ids.experimentId ?? null);
  const datasetId = ids.datasetId ?? exp?.dataset_id ?? null;
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const { data: runsRes } = useDatasetRuns(datasetId, {
    include: "config,lineage",
    enabled: shown && Boolean(datasetId),
  });
  // ⌘K `Rerun #3A with changes…` for the runs of the dataset in view.
  // dataset integration FE-0 / options-truth L5 G2: only runs Future AGI can rerun
  // (prompt, agent, HTTP) get the command; code, harness, voice and upload
  // runs have none.
  const listed = (runsRes?.results ?? [])
    .filter((r) => r.listed !== false && !r.deleted && canRerunHere(r))
    .slice(0, 24);
  useRegisterCommands(
    `rerun-with-changes:${datasetId ?? "none"}`,
    listed.map((r) => ({
      id: `rerun.${r.run_id}`,
      label: `Rerun ${r.label} with changes…`,
      keywords: ["rerun", "changes", "iterate", r.label, r.name ?? ""],
      icon: "mdi:replay",
      run: () => openRerunWithChanges({ runId: r.run_id }),
    })),
    [datasetId, listed.length],
  );
  const run = useMemo(
    () =>
      (runsRes?.results ?? []).find((r) => r.run_id === request?.runId) ?? null,
    [runsRes, request?.runId],
  );
  // dataset integration FE-0: a code / harness / voice / upload run has no real path to
  // rerun (its runner or live connection does not exist): never open it.
  const blocked = Boolean(run) && !canRerunHere(run);
  useEffect(() => {
    if (blocked) close();
  }, [blocked, close]);
  if (!shown || !request || blocked) return null;
  if (!run)
    // Loading, or a run this page can't see: say so instead of nothing.
    return (
      <Dialog
        open
        onClose={close}
        maxWidth="xs"
        fullWidth
        data-testid="rerun-dialog-pending"
      >
        <DialogTitle component="div">
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
          >
            <Typography typography="m3" fontWeight="fontWeightSemiBold">
              Rerun with changes…
            </Typography>
            <IconButton size="small" aria-label="Close" onClick={close}>
              <Iconify icon="mdi:close" width={18} />
            </IconButton>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography typography="s2" color="text.secondary">
            {datasetId && !runsRes
              ? "Loading the run…"
              : "This run isn't on this page. Open its dataset or experiment, then rerun it from there."}
          </Typography>
        </DialogContent>
      </Dialog>
    );
  return (
    <Suspense fallback={null}>
      <RerunWithChangesOpen
        run={run}
        datasetId={datasetId}
        datasetKind={datasetKind}
        siblingsOf={runsRes?.results ?? []}
        request={request}
        close={close}
      />
    </Suspense>
  );
}
