import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Link, Popover, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useActivateVersion } from "src/api/agent-playground/versions";
import { invalidateR2Reads } from "src/api/develop/r2";
import notify from "src/sections/common/ux/toast";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { kindLabel } from "src/sections/common/Runs/runKind";
import { previewLine } from "./iterateModel";
import usePublishGate from "./usePublishGate";

export const NOTHING_CHANGED =
  'Change something first, or pick "Rerun as is" to measure noise.';
/** iterate r1 G3 (P3): the draft is the version that just ran. */
export const RAN_ALREADY =
  "Change something to run a new version — or use Rerun as is";
/** options-truth L5 G2: the line under the only action, Rerun as is. */
export const AS_IS_ONLY =
  "Nothing changed yet: Rerun as is runs the same config again to measure noise. Change something to test a fix.";
export const AS_IS_RAN =
  "This version just ran: Rerun as is runs it again to measure noise. Change something to test another fix.";
// options-truth G6: the duration line states the formula it uses
// (estimateRun: attempts ÷ attempts per second), priced at the chosen model.
const estimateCopy = (label, kind, attempts, preview) =>
  `Cost per call from ${label} (same run kind: ${kindLabel(kind)}, ${attempts.toLocaleString("en-US")} attempts)${preview?.modelNote ? `, ${preview.modelNote}` : ""}. The run's own calls only: eval judge calls are not in this price. Duration = ${preview?.attempts ?? "the"} attempts ÷ ${preview?.ratePerS ? `${preview.ratePerS} attempts per second` : "the attempts per second this run takes"}. Real cost can differ by about 30%.`;

/** Column 3 · Run: price line, where it lands, the one primary action. */
const RunPane = ({ dock, pending, onRun }) => {
  const {
    preview,
    kind,
    lands,
    label,
    quickTest,
    changes,
    blockers,
    sets,
    trials,
  } = dock;
  const [howAnchor, setHowAnchor] = useState(null);
  const queryClient = useQueryClient();
  const activate = useActivateVersion();
  const external = !["agent", "prompt", "harness"].includes(kind);
  const ran = dock.draft?.ran ?? null;
  // K-3 (BL-06): the banner's publish gate (publishGate.js). A version the
  // banner already published reads as published here too (no second
  // activate of an active version).
  const gate = usePublishGate({
    datasetId: dock.parent?.dataset_id,
    baseId: dock.parent?.run_id,
    runId: ran && !ran.published && !ran.kept ? ran.runId : null,
  });
  const ranPublished = Boolean(ran?.published) || gate.state === "published";
  const gateReasonId = useId();
  const gateWhy = !ranPublished && !gate.ready ? gate.reason : null;
  const nothing = !external && (!changes.length || Boolean(dock.sameAsRan));
  const reason =
    blockers[0] ??
    (nothing ? (dock.sameAsRan ? RAN_ALREADY : NOTHING_CHANGED) : null);
  const asIsOnly = nothing && !blockers.length;
  // 8e: one label that says what happens.
  const fixRows = dock.rowIds?.length ?? 0;
  const primary = quickTest
    ? "Run quick test"
    : dock.then
      ? `Run the fix on ${fixRows} ${fixRows === 1 ? "row" : "rows"}, then the other ${dock.then.rest.length}`
      : `Run the fix on ${fixRows} ${fixRows === 1 ? "row" : "rows"}`;
  const parentAttempts = sets.all.length * (dock.parent.trials ?? trials);
  return (
    <Stack spacing={1} sx={{ minWidth: 0, height: "100%" }}>
      <Typography typography="s3" color="text.secondary" fontWeight={600}>
        Run
      </Typography>
      <Stack direction="row" alignItems="baseline" gap={0.75} flexWrap="wrap">
        <Typography
          typography="s2"
          fontWeight={500}
          data-testid="iterate-preview"
        >
          {dock.matrixLoading ? "Counting rows…" : previewLine(preview, kind)}
        </Typography>
        {kind !== "code" ? (
          <Link
            component="button"
            type="button"
            typography="s3"
            color="text.secondary"
            underline="always"
            onClick={(e) => setHowAnchor(e.currentTarget)}
          >
            How is this estimated?
          </Link>
        ) : null}
      </Stack>
      {kind === "harness" ? (
        <Typography typography="s3" color="text.secondary">
          + sandbox time, billed by the minute the harness runs
        </Typography>
      ) : null}
      {kind === "code" ? (
        <Typography typography="s3" color="text.secondary">
          Runs on your machine; model calls bill to your keys.
        </Typography>
      ) : null}
      <Popover
        open={Boolean(howAnchor)}
        anchorEl={howAnchor}
        onClose={() => setHowAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 1.5, maxWidth: 360 } } }}
      >
        <Typography typography="s2">
          {estimateCopy(label, kind, parentAttempts, preview)}
        </Typography>
      </Popover>
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="iterate-lands"
      >
        {lands.sentence}
      </Typography>
      {ran ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="iterate-last-ran"
        >
          {`Last run: ${ran.label}${ran.name ? ` (${ran.name})` : ""} · this draft is what it ran`}
        </Typography>
      ) : null}
      {/* iterate r2 G3 (P11): a draft the fix saved has a next step. */}
      {ran?.versionId && ran.name && !ran.kept ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          flexWrap="wrap"
          data-testid="iterate-draft-chip"
          sx={{
            px: 1,
            py: 0.5,
            borderRadius: 0.75,
            bgcolor: "action.hover",
          }}
        >
          <Typography typography="s2" fontWeight={500} sx={{ flex: 1 }}>
            {ranPublished
              ? `${ran.name} published · the agent's latest version`
              : `${ran.name} draft · not published`}
          </Typography>
          {!ranPublished ? (
            <>
              <CustomTooltip
                show={Boolean(gateWhy)}
                title={gateWhy ?? ""}
                size="small"
                arrow
              >
                <span>
                  <LoadingButton
                    size="small"
                    variant="outlined"
                    loading={activate.isPending}
                    disabled={!dock.canEdit || !ran.graphId || !gate.ready}
                    data-testid="iterate-dock-publish"
                    data-gate={gate.state}
                    aria-describedby={gateWhy ? gateReasonId : undefined}
                    onClick={() =>
                      activate.mutate(
                        { graphId: ran.graphId, versionId: ran.versionId },
                        {
                          onSuccess: () => {
                            dock.patch({ ran: { ...ran, published: true } });
                            invalidateR2Reads(
                              queryClient,
                              dock.parent?.dataset_id,
                            );
                            notify(`Published ${ran.name}`, {
                              variant: "success",
                              key: "iterate-publish",
                            });
                          },
                          onError: () =>
                            notify(`Couldn't publish ${ran.name}. Try again.`, {
                              variant: "error",
                              key: "iterate-publish",
                            }),
                        },
                      )
                    }
                    sx={{ height: 26, typography: "s2" }}
                  >
                    {`Publish ${ran.name}`}
                  </LoadingButton>
                </span>
              </CustomTooltip>
              <Button
                size="small"
                color="inherit"
                onClick={() => dock.patch({ ran: { ...ran, kept: true } })}
                sx={{ height: 26, typography: "s2", color: "text.secondary" }}
              >
                Keep as draft
              </Button>
            </>
          ) : null}
          {gateWhy ? (
            <Typography
              id={gateReasonId}
              typography="s3"
              color="text.secondary"
              data-testid="iterate-dock-publish-reason"
              sx={{ flexBasis: "100%" }}
            >
              {gateWhy}
            </Typography>
          ) : null}
        </Stack>
      ) : null}
      <Box sx={{ flex: 1 }} />
      <Stack direction="row" alignItems="center" gap={1}>
        {asIsOnly ? (
          // options-truth L5 G2: with nothing changed the one action is
          // Rerun as is (a new run on the same config, to measure noise).
          <LoadingButton
            variant="contained"
            color="primary"
            size="medium"
            loading={pending}
            onClick={() => onRun({ asIs: true })}
            startIcon={<Iconify icon="mdi:replay" width={18} />}
            data-testid="iterate-rerun-as-is"
          >
            Rerun as is
          </LoadingButton>
        ) : (
          <CustomTooltip
            show={Boolean(reason)}
            title={reason ?? ""}
            size="small"
            arrow
          >
            <span>
              <LoadingButton
                variant="contained"
                color="primary"
                size="medium"
                loading={pending}
                disabled={Boolean(reason)}
                onClick={() => onRun({ asIs: false })}
                startIcon={<Iconify icon="mdi:play" width={18} />}
              >
                {lands.newExperiment && !quickTest
                  ? "Test a fix in a new experiment"
                  : primary}
              </LoadingButton>
            </span>
          </CustomTooltip>
        )}
      </Stack>
      {asIsOnly && !pending ? (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="iterate-run-reason"
        >
          {dock.sameAsRan ? AS_IS_RAN : AS_IS_ONLY}
        </Typography>
      ) : reason && !pending ? (
        // A disabled Run always says why, in the panel (not only on hover).
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="iterate-run-reason"
        >
          {reason}
        </Typography>
      ) : null}
    </Stack>
  );
};

RunPane.propTypes = {
  dock: PropTypes.object.isRequired,
  pending: PropTypes.bool,
  onRun: PropTypes.func.isRequired,
};

export default RunPane;
