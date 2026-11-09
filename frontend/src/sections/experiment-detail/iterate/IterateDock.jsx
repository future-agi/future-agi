// R2 (L2) · the Iterate dock (R2-SPEC §4.3.1): change one thing, choose the
// rows, rerun as a new run, compare. One dock, many doors: every entry point
// writes the one-shot `iterate=` URL (analysisLinks.iterateHref) and this
// dock opens on it.
import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  IconButton,
  LinearProgress,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { VOCAB } from "src/sections/common/vocab";
import { enqueueSnackbar } from "src/components/snackbar";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useIterate } from "src/api/develop/r2";
import RunSourceBadge from "src/sections/common/Runs/RunSourceBadge";
import { runLabel, runShortName } from "src/sections/common/Runs/runLabel";
import { useIterateStore } from "./iterateStore";
import useIterateParams from "./useIterateParams";
import useIterateDock, { editsKey } from "./useIterateDock";
import {
  buildIterateBody,
  commitMessageOf,
  canIterate,
  nextAgentVersion,
  nextPromptVersion,
  presetOf,
} from "./iterateModel";
import {
  clampOverlayWidth,
  startEdgeDrag,
  useOverlayTop,
} from "../results/overlayPanel";
import ChangePane from "./ChangePane";
import ScopePane from "./ScopePane";
import RunPane from "./RunPane";
import usePublishGate from "./usePublishGate";
import { fullRunDoorOf, ranCandOf } from "./fullRunDoor";
import DockMoreMenu from "./DockMoreMenu";
import { comparedChipOf } from "../results/pageCandidate";
import { fixCounterText } from "./iterateStore";
import CustomTooltip from "src/components/tooltip";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";

export const DOCK_HEIGHT = 360;
/**
 * R4 8e: the dock is a right panel from this width, else a bottom panel.
 * iterate r1 G3 (P4): from 1200 px (a 1280 laptop gets the right panel,
 * not a 360 px bottom panel on a 720 px window), and it overlays the page.
 */
export const DOCK_SIDE_QUERY = "(min-width:1200px)";
// IT-R4 G3 (P2): min(560px, 44vw) — the header fits one line at 1440.
export const DOCK_SIDE_WIDTH = "min(560px, 44vw)";
/** P4: default side width in px (`DOCK_SIDE_WIDTH`), resizable. */
export const dockSideWidth = (windowW) =>
  Math.round(Math.min(560, windowW * 0.44));
const DOCK_SIDE_MIN = 380;
const NO_ANCHOR = { current: null };
const DOCK_MIN = 220;
/** Clamp a dragged bottom-panel height to [DOCK_MIN, 75% of the window]. */
export const clampDockHeight = (h, windowH) =>
  Math.round(Math.max(DOCK_MIN, Math.min(h, Math.floor(windowH * 0.75))));
export const BAR_HEIGHT = 44;
// Keeps the dock's right edge clear of the app's floating Falcon buttons.
const FAB_GUTTER = 9;

const EDITABLE =
  "input,textarea,select,[contenteditable=''],[contenteditable='true'],.monaco-editor";
const modalOpen = () =>
  Boolean(
    document.querySelector(
      ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root, .MuiDrawer-modal, .MuiModal-root:not(.MuiModal-hidden)",
    ),
  );

/** The dock's I1 body from its state (also what `Copy as API call` shows). */
function bodyOf(dock, { asIs = false, from } = {}) {
  const agentChange = dock.changes.some((c) => c.axis === "agent");
  // P3: `Rerun as is` on a draft that already ran reruns that version (the
  // same changes, no new saved version); otherwise the parent unchanged.
  const keepChanges = !asIs || dock.sameAsRan;
  // P4: `then` = the failing rows now (the rest run from the new run when
  // it finishes; iterateStore chains it).
  const then = Boolean(dock.then);
  return buildIterateBody({
    parentRunId: dock.parentId,
    quickTest: dock.quickTest,
    scopeType: dock.scopeType,
    rowIds: dock.rowIds,
    scopeLabel:
      dock.scopeType === "rows"
        ? `${dock.draft.causeId ? "Finding" : "Selection"}: ${dock.rowIds.length} rows`
        : then
          ? `${dock.scopeType === "failed" ? "Failing rows" : "These rows"} first: ${dock.rowIds.length}, then the other ${dock.then?.rest?.length ?? 0}`
          : undefined,
    trials: dock.trials,
    carryOver: then ? true : dock.draft.carryOver !== false,
    changes: keepChanges ? dock.changes : [],
    // v2.1 g4: only an agent edit saves a version (every node + limits);
    // a prompt edit runs as an unsaved edit of the parent version.
    // v2.1 g4 (action:dock:rerun-as-is): `Rerun as is` on an agent draft
    // that already ran reruns the version that run saved (its name: the
    // server reuses that version), never an unsaved `v3 (edited)`.
    saveAs:
      agentChange && (!asIs || (dock.sameAsRan && dock.draft.ran?.name))
        ? {
            agent_version_name: asIs ? dock.draft.ran.name : dock.saveName,
            status: "draft",
            // options-truth L5 G2: what the version changes, from the
            // changes themselves (`max iterations 12 → 5`).
            commit_message:
              commitMessageOf(dock.changes, dock.parent?.config) ||
              `${dock.draft.node ?? "prompt"} edited in Iterate`,
          }
        : null,
    causeId: dock.draft.causeId,
    from,
  });
}

const IterateDock = ({ datasetId, runIds, baselineId }) => {
  useIterateParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { pathname, search: locationSearch } = useLocation();
  const register = useIterateStore((s) => s.register);
  const opened = useIterateStore((s) => s.opened);
  const expanded = useIterateStore((s) => s.expanded);
  const active = useIterateStore((s) => s.active);
  const toggle = useIterateStore((s) => s.toggle);
  const openDock = useIterateStore((s) => s.open);
  const collapse = useIterateStore((s) => s.collapse);
  const setRun = useIterateStore((s) => s.setRun);
  const start = useIterateStore((s) => s.start);
  const patchDraft = useIterateStore((s) => s.patchDraft);
  const dock = useIterateDock({ datasetId, runIds, baselineId });
  // HO S7: the gate door under the dock's Publish reason (the gate RunPane
  // reads; the same query keys, nothing fetched twice).
  const ranNow = dock?.draft?.ran ?? null;
  const doorGate = usePublishGate({
    datasetId: dock?.parent?.dataset_id,
    baseId: dock?.parent?.run_id,
    runId: ranNow && !ranNow.published && !ranNow.kept ? ranNow.runId : null,
  });
  const fullRun = fullRunDoorOf({
    gate: doorGate,
    // A quick test is off R1: then the edits that ran (`ran.edits`).
    cand:
      (dock?.runs ?? []).find((r) => r.run_id === doorGate.runId) ??
      ranCandOf(dock?.parent, ranNow),
    versionName: ranNow?.name ?? null,
    path: `${pathname}${locationSearch}`,
  });
  const comparedId = comparedChipOf({
    search: locationSearch,
    runIds,
    baselineId,
    subjectId: dock?.parentId ?? null,
  });
  const iterate = useIterate();
  // dataset integration FE-0: the runs the dock can switch to (Test a fix applies).
  const switchIds = (runIds ?? []).filter((id) =>
    (dock?.runs ?? []).some((x) => x.run_id === id && canIterate(x)),
  );
  const [switchAnchor, setSwitchAnchor] = useState(null);
  const rootRef = useRef(null);
  // R4 8e: right panel at ≥ 1440 px; below, a bottom panel whose height the
  // viewer drags (remembered per user).
  const side = useMediaQuery(DOCK_SIDE_QUERY);
  const narrowHeader = useMediaQuery("(max-width:1535.95px)");
  const [dockPrefs, patchDock] = useLayoutPrefs("iterate");
  const [dragH, setDragH] = useState(null);
  const bottomH = dragH ?? dockPrefs.extras?.dockHeight ?? DOCK_HEIGHT;
  const startResize = (e) => {
    e.preventDefault();
    const y0 = e.clientY;
    const h0 = bottomH;
    let last = h0;
    const move = (ev) => {
      last = clampDockHeight(h0 + (y0 - ev.clientY), window.innerHeight);
      setDragH(last);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      patchDock({ extras: { dockHeight: last } });
      setDragH(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onResizeKey = (e) => {
    const step = e.key === "ArrowUp" ? 40 : e.key === "ArrowDown" ? -40 : 0;
    if (!step) return;
    e.preventDefault();
    patchDock({
      extras: {
        dockHeight: clampDockHeight(bottomH + step, window.innerHeight),
      },
    });
  };
  const sidePanel = side && expanded;
  // P4: the right panel overlays the page below its header (the page keeps
  // its widths); drag the left edge to resize (remembered per user).
  const sideTop = useOverlayTop(NO_ANCHOR, sidePanel);
  const [dragW, setDragW] = useState(null);
  const sideW = clampOverlayWidth(
    dragW ?? dockPrefs.extras?.sideWidth ?? dockSideWidth(window.innerWidth),
    window.innerWidth,
    DOCK_SIDE_MIN,
  );
  const from = pathname.endsWith("/matrix") ? "matrix" : undefined;
  // iterate r2 G3 (P5, rule 10): the collapsed bar floats over the page's
  // bottom gutter (the main's bottom padding), so the grid keeps its height.
  const barOnly = !expanded;
  const [barLeft, setBarLeft] = useState(null);
  useEffect(() => {
    if (!barOnly) return undefined;
    const measure = () => {
      const main = rootRef.current?.closest?.("main");
      const left = main?.getBoundingClientRect?.().left;
      setBarLeft(Number.isFinite(left) ? Math.round(left) : null);
    };
    measure();
    window.addEventListener("resize", measure);
    const t = setTimeout(measure, 300);
    return () => {
      window.removeEventListener("resize", measure);
      clearTimeout(t);
    };
  }, [barOnly, opened, active]);

  useEffect(() => {
    register(navigate, queryClient);
  }, [register, navigate, queryClient]);

  // L7b G3: a run Test a fix cannot change (code, harness, voice) never
  // opens an empty dock: the dock stays collapsed to its bar.
  const nothingToChange = Boolean(dock.ready && !(dock.tabs ?? []).length);
  useEffect(() => {
    if (nothingToChange && expanded) collapse();
  }, [nothingToChange, expanded, collapse]);

  // `I` toggles (never while typing); Esc leaves the editor, then collapses.
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      const t = e.target;
      const typing = Boolean(t?.closest?.(EDITABLE));
      if (e.key === "Escape") {
        if (typing && rootRef.current?.contains(t)) {
          t.blur();
          return;
        }
        if (!typing && !modalOpen() && useIterateStore.getState().expanded)
          collapse();
        return;
      }
      if ((e.key === "i" || e.key === "I") && !typing && !modalOpen()) {
        e.preventDefault();
        // options-truth L5 G2: the first `I` opens on every row, like the
        // header's Test a fix; later presses fold and unfold the dock.
        if (!useIterateStore.getState().opened)
          openDock({ entry: { runId: null, scope: "all" } });
        else toggle();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggle, collapse, openDock]);

  if (!opened && !active) return null;

  const run = ({ asIs }) => {
    const body = bodyOf(dock, { asIs, from });
    const parentId = dock.parentId;
    const preset = presetOf(null, dock.draft.causeId);
    iterate.mutate(
      { experimentId: dock.parent.experiment_id, datasetId, ...body },
      {
        onSuccess: (result) => {
          const compareUrl =
            result.compare_url ??
            `/dashboard/develop/${datasetId}/compare/${from ?? "data"}?runs=${parentId},${result.run_id}&baseline=${parentId}&from=iterate`;
          start(result, {
            datasetId,
            baseId: parentId,
            startedPath: pathname,
            compareUrl,
            // P2: the failure this fix aims at, so the result banner says
            // `Target failure fixed on 6 of 6 rows` (its rows and words).
            cause: dock.draft.causeId
              ? {
                  id: dock.draft.causeId,
                  title: preset?.cause ?? null,
                  fixed: preset?.fixed ?? null,
                  label: dock.cause?.label ?? null,
                  rows: dock.cause?.rows?.length
                    ? dock.cause.rows
                    : dock.scopeType === "rows"
                      ? dock.rowIds
                      : null,
                }
              : null,
            noise: Boolean(result.script?.noise),
            pathId: dock.draft.pathId ?? null,
            // P4: then rerun the other rows from this run (one verdict).
            then: dock.then?.rest?.length
              ? {
                  rows: dock.then.rest,
                  trials: dock.trials,
                  experimentId: dock.parent.experiment_id,
                  tab: from ?? "data",
                }
              : null,
          });
          // P3: the draft stays (reopening starts from what just ran); the
          // next save gets the next version name.
          if (!asIs && dock.changes.length) {
            // v2.1 g4: the name the server saved (a taken name moves on).
            const ranName = result.agent_version?.name ?? dock.saveName;
            patchDraft(parentId, {
              ran: {
                edits: editsKey(dock.parent, dock.draft),
                runId: result.run_id,
                label: result.label,
                name: ranName,
                // P11: the draft version it saved (Publish / Keep as draft).
                versionId: result.agent_version?.id ?? null,
                graphId: dock.parent.config?.agent?.graph_id ?? null,
                published: false,
                kept: false,
              },
              saveName: dock.parent.config?.agent
                ? nextAgentVersion(ranName)
                : nextPromptVersion(ranName),
            });
          }
        },
        onError: (err) =>
          enqueueSnackbar(
            `Couldn't start the iteration: ${getRequestErrorMessage(err, "try again")}`,
            { variant: "error" },
          ),
      },
    );
  };

  const header = (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        height: BAR_HEIGHT,
        pl: 2,
        pr: sidePanel ? 1.5 : FAB_GUTTER,
        flexShrink: 0,
        minWidth: 0,
      }}
    >
      <Iconify
        icon="mdi:source-branch"
        width={18}
        sx={{ color: "primary.main" }}
      />
      {dock.ready ? (
        <>
          <Button
            size="small"
            color="inherit"
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            onClick={(e) => setSwitchAnchor(e.currentTarget)}
            disabled={switchIds.length < 2}
            sx={{
              typography: "s1",
              fontWeight: 600,
              px: 0.75,
              minWidth: 0,
              flexShrink: 0,
              whiteSpace: "nowrap",
              // A single run has no other run to switch to: the title reads
              // as a title, not a greyed control.
              "&.Mui-disabled": { color: "text.primary" },
            }}
          >
            {`${VOCAB.TEST_A_FIX} on ${dock.label}`}
          </Button>
          <Typography
            typography="s1"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0 }}
          >
            {runShortName(dock.parent)}
          </Typography>
          <RunSourceBadge run={dock.parent} />
          {/* iterate r3 G3 (S2): the lane the subject was compared with is
              a chip, never the page's baseline or candidate. */}
          {comparedId ? (
            <Chip
              size="small"
              variant="outlined"
              label={`compared with ${dock.runs.find((x) => x.run_id === comparedId)?.label ?? "another run"}`}
              data-testid="iterate-compared"
              sx={{ height: 22, typography: "s3", flexShrink: 0 }}
            />
          ) : null}
          {/* P3: the door's page (the trace compare) is one click away. */}
          {dock.back && narrowHeader ? (
            // IT-R4 G3 (P2): icon-only below 1536 px so the header stays
            // one line (the label is the tooltip and the aria-label).
            <CustomTooltip
              show
              size="small"
              placement="bottom"
              arrow
              title="Back to the trace"
            >
              <IconButton
                size="small"
                onClick={() => navigate(dock.back)}
                aria-label="Back to the trace"
                data-testid="iterate-back"
                sx={{ color: "primary.main", flexShrink: 0 }}
              >
                <Iconify icon="mdi:arrow-left" width={16} />
              </IconButton>
            </CustomTooltip>
          ) : dock.back ? (
            <Button
              size="small"
              color="inherit"
              onClick={() => navigate(dock.back)}
              startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
              data-testid="iterate-back"
              sx={{
                typography: "s2",
                color: "primary.main",
                px: 0.75,
                minWidth: 0,
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              Back to the trace
            </Button>
          ) : null}
          <Menu
            anchorEl={switchAnchor}
            open={Boolean(switchAnchor)}
            onClose={() => setSwitchAnchor(null)}
          >
            {switchIds.map((id) => {
              const r = dock.runs.find((x) => x.run_id === id);
              if (!r) return null;
              return (
                <MenuItem
                  key={id}
                  selected={id === dock.parentId}
                  sx={{ typography: "s2", gap: 1 }}
                  onClick={() => {
                    setSwitchAnchor(null);
                    setRun(id);
                  }}
                >
                  <Typography typography="s2" fontWeight={600}>
                    {runLabel(r)}
                  </Typography>
                  <Typography typography="s2" color="text.secondary" noWrap>
                    {runShortName(r)}
                  </Typography>
                </MenuItem>
              );
            })}
          </Menu>
        </>
      ) : (
        <Typography
          typography="s1"
          color="text.secondary"
          data-testid="iterate-no-run"
        >
          {dock.loading
            ? "Loading runs…"
            : dock.noIterable
              ? `${VOCAB.TEST_A_FIX} works on prompt and agent runs; this experiment has none. Rerun its runs from the run menu.`
              : VOCAB.TEST_A_FIX}
        </Typography>
      )}
      <Box sx={{ flex: 1 }} />
      {active ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="iterate-progress"
        >
          <Typography
            typography="s2"
            fontWeight={500}
            noWrap
            title={fixCounterText(active)}
            sx={{ minWidth: 0 }}
          >
            {/* IT-R4 G3 (P2): the same counter as the results toolbar. */}
            {`${active.label} · ${fixCounterText(active, { short: sidePanel })}`}
          </Typography>
          <LinearProgress
            variant="determinate"
            value={active.attempts ? (100 * active.done) / active.attempts : 0}
            sx={{ width: 120, height: 4, borderRadius: 2 }}
          />
        </Stack>
      ) : null}
      {expanded && dock.ready ? <DockMoreMenu dock={dock} /> : null}
      <Button
        size="small"
        color="inherit"
        onClick={toggle}
        endIcon={
          <Iconify
            icon={expanded ? "mdi:chevron-down" : "mdi:chevron-up"}
            width={16}
          />
        }
        aria-label={expanded ? "Collapse" : "Expand"}
        sx={{
          typography: "s2",
          color: "text.secondary",
          minWidth: 0,
          flexShrink: 0,
          ...(sidePanel && { "& .MuiButton-endIcon": { ml: 0 } }),
        }}
      >
        {sidePanel ? null : expanded ? "Collapse" : "Expand"}
      </Button>
    </Stack>
  );

  return (
    <Box
      ref={rootRef}
      data-testid="iterate-dock"
      data-dock-side={sidePanel ? "right" : "bottom"}
      data-fa-overlay-panel=""
      sx={{
        ...(sidePanel
          ? {
              position: "fixed",
              top: sideTop,
              right: 0,
              bottom: 0,
              width: sideW,
              zIndex: 1100,
              borderLeft: "1px solid",
            }
          : barOnly && barLeft != null
            ? {
                position: "fixed",
                bottom: 0,
                left: barLeft,
                right: 0,
                zIndex: 5,
                height: BAR_HEIGHT,
              }
            : {
                position: "sticky",
                bottom: 0,
                zIndex: 5,
                height: expanded ? bottomH : BAR_HEIGHT,
              }),
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        boxShadow: (theme) =>
          `0 -4px 16px ${theme.palette.mode === "dark" ? "rgba(0,0,0,0.4)" : "rgba(0,0,0,0.06)"}`,
      }}
    >
      {sidePanel ? (
        <Box
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the Test a fix panel (← / →)"
          tabIndex={0}
          onPointerDown={(e) =>
            startEdgeDrag(e, {
              width: sideW,
              min: DOCK_SIDE_MIN,
              onWidth: setDragW,
              onDone: (w) => {
                patchDock({ extras: { sideWidth: w } });
                setDragW(null);
              },
            })
          }
          onKeyDown={(e) => {
            const step =
              e.key === "ArrowLeft" ? 40 : e.key === "ArrowRight" ? -40 : 0;
            if (!step) return;
            e.preventDefault();
            patchDock({
              extras: {
                sideWidth: clampOverlayWidth(
                  sideW + step,
                  window.innerWidth,
                  DOCK_SIDE_MIN,
                ),
              },
            });
          }}
          sx={{
            position: "absolute",
            left: -3,
            top: 0,
            bottom: 0,
            width: 6,
            cursor: "col-resize",
            zIndex: 1,
            "&:hover, &:focus-visible": {
              bgcolor: (t) => `${t.palette.primary.main}33`,
              outline: "none",
            },
          }}
        />
      ) : null}
      {expanded && !sidePanel ? (
        <Box
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize the Test a fix panel (↑ / ↓)"
          tabIndex={0}
          onPointerDown={startResize}
          onKeyDown={onResizeKey}
          sx={{
            position: "absolute",
            top: -3,
            left: 0,
            right: 0,
            height: 6,
            cursor: "row-resize",
            zIndex: 1,
            "&:hover, &:focus-visible": {
              bgcolor: (t) => `${t.palette.primary.main}33`,
              outline: "none",
            },
          }}
        />
      ) : null}
      {header}
      {expanded && dock.ready ? (
        <Stack
          direction={sidePanel ? "column" : "row"}
          gap={sidePanel ? 1.5 : 2.5}
          sx={{
            flex: 1,
            minHeight: 0,
            pl: 2,
            pr: sidePanel ? 2 : FAB_GUTTER,
            pb: 1.5,
            "& > *": { minHeight: 0 },
            ...(sidePanel && {
              overflowY: "auto",
              "& > *": { minHeight: "auto", flex: "0 0 auto !important" },
              "& > * + *": {
                borderLeft: "none !important",
                pl: "0 !important",
                borderTop: "1px solid",
                borderColor: "divider",
                pt: 1.5,
              },
            }),
          }}
        >
          <Box sx={{ flex: 1.5, minWidth: 0 }}>
            <ChangePane dock={dock} />
          </Box>
          <Box
            sx={{
              flex: 1,
              minWidth: 0,
              overflow: "auto",
              borderLeft: "1px solid",
              borderColor: "divider",
              pl: 2.5,
            }}
          >
            <ScopePane dock={dock} />
          </Box>
          <Box
            data-testid="iterate-run-pane"
            sx={{
              flex: 1,
              minWidth: 0,
              borderLeft: "1px solid",
              borderColor: "divider",
              pl: 2.5,
              // IT-R5 G3 (P4): the Run button stays in view on a 900 px
              // high window; the change and scope scroll under it.
              ...(sidePanel && {
                position: "sticky",
                bottom: 0,
                zIndex: 1,
                bgcolor: "background.paper",
                pb: 0.5,
                boxShadow: (theme) =>
                  `0 -6px 12px -8px ${theme.palette.mode === "dark" ? "rgba(0,0,0,0.6)" : "rgba(0,0,0,0.18)"}`,
              }),
            }}
          >
            {fullRun ? (
              <Button
                size="small"
                variant="outlined"
                data-testid="iterate-dock-full-run"
                startIcon={
                  <Iconify icon="mdi:play-circle-outline" width={16} />
                }
                onClick={() => {
                  patchDraft(fullRun.baseId, fullRun.draft);
                  navigate(fullRun.href);
                }}
                sx={{ mb: 0.75, typography: "s2", whiteSpace: "nowrap" }}
              >
                {fullRun.label}
              </Button>
            ) : null}
            <RunPane dock={dock} pending={iterate.isPending} onRun={run} />
          </Box>
        </Stack>
      ) : null}
    </Box>
  );
};

IterateDock.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  runId: PropTypes.string,
  baselineId: PropTypes.string,
  trial: PropTypes.number,
  rowId: PropTypes.string,
};

export default IterateDock;
