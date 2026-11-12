import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import useRunActions from "src/sections/common/Runs/useRunActions";
import { canResumeRun } from "src/sections/common/Runs/runResume";
import { SimulatedTag } from "src/sections/common/RunSources/TodayStatus";
import { WAIT_FOR } from "src/sections/develop-detail/Experiment/waitingModel";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import {
  sideBySideDisabledReason,
  sideBySideHref,
  sideBySideTitle,
} from "./sideBySide";
import {
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import {
  ITERATE_TIP,
  VIEWER_TIP,
} from "src/sections/experiment-detail/iterate/IterateButton";
import { canIterate } from "src/sections/experiment-detail/iterate/iterateModel";
import {
  compareSelection,
  compareUrl,
  experimentUrl,
  experimentRunsOf,
  promptUrl,
} from "./leaderboardModel";
import { canRerunHere, defaultRunsTab } from "src/sections/common/Runs/runKind";
import { agentLinkOf } from "src/sections/common/RunSettings/targetLinks";
import { useCell } from "./cellContext";
import { promptDoorState } from "src/sections/workbench/createPrompt/workbenchPrefill";
import { canRunOf, deleteTip, editTip, runTip } from "./leaderboardAccess";

// the interface contract §9 (M6): edit and run doors read the tab's access gates and tips.

const Item = ({
  icon,
  label,
  onClick,
  disabled,
  danger,
  tip,
  endIcon,
  tag,
}) => {
  const item = (
    <MenuItem
      disabled={disabled}
      onClick={onClick}
      sx={{ typography: "s1", color: danger ? "error.main" : undefined }}
    >
      <ListItemIcon sx={{ minWidth: 28, color: "inherit" }}>
        <Iconify icon={icon} width={16} />
      </ListItemIcon>
      <ListItemText
        primary={label}
        primaryTypographyProps={{ typography: "s1" }}
      />
      {tag}
      {endIcon && (
        <Iconify
          icon={endIcon}
          width={16}
          sx={{ ml: 1, color: "text.secondary" }}
        />
      )}
    </MenuItem>
  );
  if (!tip) return item;
  return (
    <CustomTooltip show title={tip} placement="left" size="small" arrow>
      <span>{item}</span>
    </CustomTooltip>
  );
};

Item.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
  danger: PropTypes.bool,
  tip: PropTypes.string,
  endIcon: PropTypes.string,
  tag: PropTypes.node,
};

/** Run actions (§3.7): one run, plus the experiment's when it is alone. */
function RunMenuItems({ run, context, close, isOnlyRun, openRerun }) {
  const actions = useRunActions(run);
  const status = getRunStatus(run);
  const prog = runProgressOf(run);
  const isBaseline = run.run_id === context.baselineId;
  const isPinned = run.run_id === context.pinnedRunId;
  const go = (fn) => () => {
    close();
    fn();
  };
  const canEdit = context.canEdit;
  const canRun = canRunOf(context);
  const external = run.target_type === "external";
  const compare = compareSelection([run], context.baselineId, {
    pinnedId: context.pinnedRunId,
    candId: context.headlineCandId,
  });
  // IT-R5 G2 P2: the pinned baseline vs its own previous run.
  const prev = isPinned ? context.numbers?.prevOf?.[run.run_id] : null;
  return [
    ...(prev
      ? [
          <Item
            key="prev"
            icon="mdi:history"
            label={`Compare with previous (${prev.previous.label})`}
            onClick={go(() =>
              context.navigate(
                compareUrl(context.datasetId, {
                  runIds: [prev.previous.run_id, run.run_id],
                  baselineId: prev.previous.run_id,
                  candId: run.run_id,
                  runs: [prev.previous, run],
                  from: context.fromUrl,
                  tab: "data",
                }),
              ),
            )}
          />,
        ]
      : []),
    <Item
      key="open"
      icon="mdi:open-in-app"
      label="Open"
      onClick={go(() =>
        context.navigate(
          experimentUrl(context.datasetId, run.experiment_id, {
            runId: run.run_id,
          }),
        ),
      )}
    />,
    <Item
      key="compare"
      icon="mdi:compare-horizontal"
      label="Compare with baseline run"
      disabled={!!compare.error}
      tip={compare.error ?? undefined}
      onClick={go(() =>
        context.navigate(
          compareUrl(context.datasetId, { ...compare, from: context.fromUrl }),
        ),
      )}
    />,
    <Item
      key="side"
      icon="mdi:view-split-vertical"
      label="Compare side by side"
      disabled={!sideBySideHref(run, context)}
      tip={
        sideBySideDisabledReason(run, context) ??
        sideBySideTitle(run, context) ??
        undefined
      }
      onClick={go(() => context.navigate(sideBySideHref(run, context)))}
    />,
    <Item
      key="pin"
      icon={isPinned ? "mdi:pin-off-outline" : "mdi:pin-outline"}
      label={isPinned ? "Unpin baseline" : "Make baseline"}
      disabled={!canEdit}
      tip={canEdit ? undefined : editTip(context)}
      onClick={go(() =>
        isPinned ? context.unpinBaseline() : context.pinBaseline(run.run_id),
      )}
    />,
    <Divider key="d1" />,
    // dataset integration FE-0: Test a fix only for a prompt or agent run.
    ...(canIterate(run)
      ? [
          <Item
            key="iterate"
            icon="mdi:source-branch"
            label="Test a fix…"
            // L7c g5 (access:ws-level): Test a fix creates runs, so it needs
            // the run permission (Edit + run), not edit alone.
            disabled={!canRun}
            tip={canRun ? ITERATE_TIP : canEdit ? runTip(context) : VIEWER_TIP}
            onClick={go(() =>
              context.navigate(
                iterateHref(
                  // IT-R2 G2 P1: over the Matrix for an experiment of 3+ runs.
                  `/dashboard/develop/experiment/${run.experiment_id}/${defaultRunsTab(experimentRunsOf(context.allRuns, run.experiment_id))}`,
                  { runId: run.run_id, scope: "all" },
                ),
              ),
            )}
          />,
        ]
      : []),
    // dataset integration FE-0: no Rerun for a run whose only path was a generated
    // runner or a live connection (code, harness, voice, upload).
    ...(canRerunHere(run)
      ? [
          <Item
            key="rerun"
            icon="mdi:refresh"
            label="Rerun"
            endIcon="mdi:chevron-right"
            disabled={!canRun || external}
            tip={
              !canRun
                ? runTip(context)
                : external
                  ? "External runs rerun from your CI"
                  : undefined
            }
            onClick={(e) => openRerun(e.currentTarget, actions)}
          />,
          <Item
            key="rerunChanges"
            icon="mdi:tune-variant"
            label="Rerun with changes…"
            disabled={!canRun}
            tip={
              canRun
                ? "Change the model, settings, recipe or rows, then rerun · ⇧R"
                : runTip(context)
            }
            onClick={go(() => openRerunWithChanges({ runId: run.run_id }))}
          />,
        ]
      : []),
    // Run control off: this run's own Stop and Resume stay, disabled, with
    // the reason (the experiment page's Stop still stops the experiment).
    status.active ? (
      <Item
        key="stop"
        icon="material-symbols:stop-rounded"
        label="Stop"
        disabled={!canRun || actions.runControlOff}
        tip={
          !canRun
            ? runTip(context)
            : actions.runControlOff
              ? actions.held.stop
              : undefined
        }
        onClick={go(actions.stop)}
      />
    ) : status.key === "Cancelled" && prog.left > 0 && canResumeRun(run) ? (
      <Item
        key="resume"
        icon="material-symbols:play-arrow-rounded"
        label={`Resume · ${prog.left} left`}
        disabled={!canRun || actions.runControlOff}
        tip={
          !canRun
            ? runTip(context)
            : actions.runControlOff
              ? actions.held.resume
              : undefined
        }
        onClick={go(actions.resume)}
      />
    ) : null,
    run.target_type === "prompt" ? (
      <Item
        key="prompts"
        icon="mdi:open-in-new"
        label="Open in Prompts"
        disabled={!promptUrl(run)}
        onClick={go(() =>
          context.navigate(promptUrl(run), { state: promptDoorState(run) }),
        )}
      />
    ) : run.target_type === "agent" ? (
      <Item
        key="agent"
        icon="mdi:open-in-new"
        label="Open in Agent Playground"
        disabled={!agentPlaygroundUrl(run)}
        onClick={go(() => context.navigate(agentPlaygroundUrl(run)))}
      />
    ) : external && status.waiting ? (
      // options-truth L7b G4 (upload:how-to-upload): uploading from your
      // code is absent, so a run waiting for results offers the labelled
      // stand-in that plays them in (the same path as the waiting banner);
      // a finished upload run offers nothing here.
      <Item
        key="simulateUpload"
        icon="mdi:cloud-upload-outline"
        label={WAIT_FOR.upload.label}
        disabled={!canRun}
        tip={canRun ? undefined : runTip(context)}
        tag={<SimulatedTag event={WAIT_FOR.upload.event} />}
        onClick={go(actions.simulateUpload)}
      />
    ) : null,
    <Divider key="d2" />,
    <Item
      key="copy"
      icon="mdi:content-copy"
      label="Copy run ID"
      onClick={go(() => context.copyText(run.run_id, "Copied run id"))}
    />,
    <Item
      key="copyName"
      icon="mdi:content-copy"
      label="Copy experiment name"
      onClick={go(() =>
        context.copyText(run.experiment_name ?? "", "Copied experiment name"),
      )}
    />,
    <Item
      key="csv"
      icon="mdi:download-outline"
      label="Download CSV"
      disabled={context.canDownload === false}
      tip={context.canDownload === false ? context.tips?.download : undefined}
      onClick={go(() =>
        context.downloadCsv({
          id: run.experiment_id,
          name: run.experiment_name,
        }),
      )}
    />,
    ...(isOnlyRun
      ? experimentItems({
          experimentId: run.experiment_id,
          name: run.experiment_name,
          description: run.experiment_description,
          runCount: 1,
          context,
          close,
        })
      : [
          // L2 G9: a run of a multi-run experiment duplicates its experiment
          // (every run's setup comes back as its card).
          <Item
            key="duplicate"
            icon="mdi:content-duplicate"
            label={`Duplicate & edit${run.experiment_ordinal ? ` #${run.experiment_ordinal}` : ""}`}
            disabled={!context.canEdit || !run.experiment_id}
            tip={context.canEdit ? undefined : editTip(context)}
            onClick={go(() => context.duplicate(run.experiment_id))}
          />,
        ]),
    isBaseline ? null : null,
  ].filter(Boolean);
}

function experimentItems({
  experimentId,
  name,
  description,
  runCount,
  context,
  close,
}) {
  const go = (fn) => () => {
    close();
    fn();
  };
  return [
    <Divider key="d3" />,
    <Item
      key="duplicate"
      icon="mdi:content-duplicate"
      label="Duplicate & edit"
      disabled={!context.canEdit}
      tip={context.canEdit ? undefined : editTip(context)}
      onClick={go(() => context.duplicate(experimentId))}
    />,
    <Item
      key="rename"
      icon="mdi:pencil-outline"
      label="Edit…"
      disabled={!context.canEdit}
      tip={context.canEdit ? undefined : editTip(context)}
      onClick={go(() =>
        context.openRename({ id: experimentId, name, description }),
      )}
    />,
    <Item
      key="delete"
      icon="mdi:trash-can-outline"
      label="Delete"
      danger
      disabled={!context.canDelete}
      tip={context.canDelete ? undefined : deleteTip(context)}
      onClick={go(() =>
        context.requestDelete({ ids: [experimentId], runCount, name }),
      )}
    />,
  ];
}

function GroupMenuItems({ runs, context, close }) {
  const first = runs[0];
  const go = (fn) => () => {
    close();
    fn();
  };
  const compare = compareSelection(runs, context.baselineId, {
    pinnedId: context.pinnedRunId,
    candId: context.headlineCandId,
  });
  return [
    <Item
      key="open"
      icon="mdi:open-in-app"
      label="Open experiment"
      onClick={go(() =>
        context.navigate(
          experimentUrl(context.datasetId, first.experiment_id, {
            runs: experimentRunsOf(context.allRuns, first.experiment_id),
          }),
        ),
      )}
    />,
    <Item
      key="compare"
      icon="mdi:compare-horizontal"
      label={`Compare ${runs.length} runs`}
      disabled={!!compare.error}
      tip={compare.error ?? undefined}
      onClick={go(() =>
        context.navigate(
          compareUrl(context.datasetId, { ...compare, from: context.fromUrl }),
        ),
      )}
    />,
    // dataset integration FE-0: only the runs Future AGI can start again itself.
    ...(runs.some(canRerunHere)
      ? [
          <Item
            key="rerun"
            icon="mdi:refresh"
            label="Rerun all runs"
            disabled={!canRunOf(context)}
            tip={canRunOf(context) ? undefined : runTip(context)}
            onClick={go(() =>
              // options-truth L7b G4: the confirm names the runs it skips.
              context.requestRerun({
                mode: "all",
                runs: runs.filter(canRerunHere),
                skipped: runs.filter((r) => !canRerunHere(r)),
              }),
            )}
          />,
        ]
      : []),
    <Item
      key="csv"
      icon="mdi:download-outline"
      label="Download CSV"
      disabled={context.canDownload === false}
      tip={context.canDownload === false ? context.tips?.download : undefined}
      onClick={go(() =>
        context.downloadCsv({
          id: first.experiment_id,
          name: first.experiment_name,
        }),
      )}
    />,
    ...experimentItems({
      experimentId: first.experiment_id,
      name: first.experiment_name,
      description: first.experiment_description,
      runCount: runs.length,
      context,
      close,
    }),
  ];
}

/**
 * options-truth L5 G2: the Agent Playground builder on the run's own graph
 * version (`?version=<version id>`, what the playground reads), or null.
 * HO S8: the version the run ran, as Publish reads it (a Test a fix child
 * names its saved version in its config; its target is the parent's).
 */
export function agentPlaygroundUrl(run) {
  // M9: the one link rule (RunSettings/targetLinks.agentLinkOf): the version
  // the run ran, so a Test a fix child opens v3.1, not its parent's v3.
  return agentLinkOf(run?.target, { config: run?.config });
}

/**
 * options-truth G6: a run has rows to rerun when a row errored, the run was
 * stopped with rows left, or a row failed an eval (checks included): its
 * pass rate (`pass_k` / headline) is below 100%.
 */
export function hasFailingRows(run, numbers) {
  if (!run) return false;
  if (runProgressOf(run).errors) return true;
  if (getRunStatus(run).key === "Cancelled") return true;
  const rate = numbers?.rate?.rate ?? numbers?.rate?.value ?? numbers?.rate;
  const passK = numbers?.passK?.rate;
  return [rate, passK].some((v) => Number.isFinite(v) && v < 1);
}

/** The row's `⋯` (last hover action): run menu on run rows, experiment
 * menu on group rows. */
const RunRowMenu = (params) => {
  const { node, data, context } = useCell(params);
  const [anchor, setAnchor] = useState(null);
  const [rerunAnchor, setRerunAnchor] = useState(null);
  const [rerunActions, setRerunActions] = useState(null);
  // IT-R5 G2 P2: the pinned baseline row has its menu too.
  if (node.rowPinned && !data) return null;
  const runs = node.group
    ? (node.allLeafChildren ?? []).map((n) => n.data).filter(Boolean)
    : data
      ? [data]
      : [];
  if (!runs.length) return null;
  if (node.group && context.group !== "experiment") return null;
  const close = () => {
    setAnchor(null);
    setRerunAnchor(null);
    params.onHostClose?.();
  };
  const isOnlyRun =
    !node.group && (context.runsPerExperiment[data.experiment_id] ?? 1) === 1;
  const rerun = (mode) => () => {
    const run = runs[0];
    close();
    context.requestRerun({ mode, runs: [run] });
  };
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      sx={{ height: params.hostMenu ? 0 : "100%" }}
    >
      {!params.hostMenu && (
        <IconButton
          size="small"
          aria-label={
            node.group ? "Experiment actions" : `${data.label} actions`
          }
          aria-haspopup="menu"
          aria-expanded={Boolean(
            anchor ||
              rerunAnchor ||
              (context.rowMenu?.group === Boolean(node.group) &&
                context.rowMenu?.runIds[0] === runs[0].run_id),
          )}
          onClick={(e) => {
            e.stopPropagation();
            if (context.openRowMenu) {
              const rect = e.currentTarget.getBoundingClientRect();
              context.openRowMenu({
                group: Boolean(node.group),
                experimentId: runs[0].experiment_id,
                runIds: runs.map((run) => run.run_id),
                position: { top: rect.bottom, left: rect.right },
              });
            } else {
              setAnchor(e.currentTarget);
            }
          }}
          sx={{
            color: "text.secondary",
            bgcolor: "background.paper",
            width: 26,
            height: 26,
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
      )}
      <Menu
        anchorEl={anchor}
        anchorReference={params.hostMenu ? "anchorPosition" : "anchorEl"}
        anchorPosition={params.hostMenu?.position}
        open={!!params.hostMenu || !!anchor}
        onClose={close}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 230 } } }}
      >
        {node.group ? (
          <GroupMenuItems runs={runs} context={context} close={close} />
        ) : (
          <RunMenuItems
            run={data}
            context={{ ...context, menuAnchor: () => anchor }}
            close={close}
            isOnlyRun={isOnlyRun}
            openRerun={(el, actions) => {
              setRerunActions(actions);
              setRerunAnchor(el);
            }}
          />
        )}
      </Menu>
      <Menu
        anchorEl={rerunAnchor}
        open={!!rerunAnchor && !!rerunActions}
        onClose={() => setRerunAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 200 } } }}
      >
        <Item
          icon="mdi:refresh"
          label="Rerun all rows"
          onClick={rerun("all")}
        />
        <Item
          icon="mdi:alert-circle-outline"
          label="Rerun failed rows"
          disabled={
            !hasFailingRows(runs[0], context.numbers?.byRun?.[runs[0].run_id])
          }
          tip={
            hasFailingRows(runs[0], context.numbers?.byRun?.[runs[0].run_id])
              ? undefined
              : "No failed rows"
          }
          onClick={rerun("failed")}
        />
        <Item
          icon="mdi:scale-balance"
          label="Re-score evals"
          onClick={rerun("rescore")}
        />
      </Menu>
    </Stack>
  );
};

RunRowMenu.propTypes = {
  node: PropTypes.object,
  data: PropTypes.object,
  context: PropTypes.object,
  hostMenu: PropTypes.object,
  onHostClose: PropTypes.func,
};

RunMenuItems.propTypes = {
  run: PropTypes.object.isRequired,
  context: PropTypes.object.isRequired,
  close: PropTypes.func.isRequired,
  isOnlyRun: PropTypes.bool,
  openRerun: PropTypes.func.isRequired,
};

GroupMenuItems.propTypes = {
  runs: PropTypes.array.isRequired,
  context: PropTypes.object.isRequired,
  close: PropTypes.func.isRequired,
};

export default RunRowMenu;
