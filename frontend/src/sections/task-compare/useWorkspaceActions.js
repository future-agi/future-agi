// Side by side · actions: keyboard (G0 `taskCompare` surface), palette
// commands, review writes (viewed / verdict / notes, optimistic + Undo),
// copy link and navigation. The component wires the returned handlers.
import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTaskReviewMutation } from "src/api/develop/r3";
import useShortcuts, {
  openShortcutSheet,
} from "src/sections/common/ux/useShortcuts";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import { matrixHref } from "src/sections/common/Analysis/analysisLinks";
import {
  linkToast,
  stepRow,
  VERDICT_CHOICES,
  verdictToast,
} from "./workspaceModel";

export default function useWorkspaceActions({
  datasetId,
  st,
  d,
  focusIdx,
  setFocusIdx,
  ui,
}) {
  const navigate = useNavigate();
  const { params: p } = st;
  const mutation = useTaskReviewMutation();
  const [optimistic, setOptimistic] = useState({});
  const [revealed, setRevealed] = useState(false);
  const noteRef = useRef(null);
  const railSearchRef = useRef(null);
  const stepSearchRef = useRef(null);

  const rowLabel = d.row?.label ?? "this task";
  const lanes = d.lanes;
  const left = lanes[0];
  const right =
    lanes[Math.max(1, Math.min(focusIdx, lanes.length - 1))] ?? null;
  const opt = optimistic[d.rowId] ?? {};
  const viewed = opt.viewed ?? d.review?.viewed ?? false;
  const myVerdicts = (d.review?.verdicts ?? []).filter(
    (v) => v.author === ui.me,
  );
  const current =
    opt.verdict === null
      ? null
      : opt.verdict ?? myVerdicts[myVerdicts.length - 1] ?? null;

  const write = useCallback(
    (body, after) =>
      mutation.mutate(
        { datasetId, row_id: d.rowId, run_ids: d.laneIds, ...body },
        {
          onSettled: () => setOptimistic((o) => ({ ...o, [d.rowId]: {} })),
          onError: () =>
            notify("Couldn't save. Check your connection and try again.", {
              variant: "error",
              key: "tc-save",
            }),
          onSuccess: after,
        },
      ),
    [mutation, datasetId, d.rowId, d.laneIds],
  );

  const backHref = useMemo(() => {
    if (p.from && p.from.startsWith("/")) return p.from;
    return matrixHref({
      mode: "compare",
      datasetId,
      runIds: d.selection,
      baselineId: d.baselineId,
      row: d.rowId,
    });
  }, [p.from, datasetId, d.selection, d.baselineId, d.rowId]);
  const backLabel = /\/matrix/.test(backHref)
    ? "Matrix"
    : /\/summary/.test(backHref)
      ? "Summary"
      : /\/data/.test(backHref)
        ? "Results"
        : "Back";
  const goBack = useCallback(() => navigate(backHref), [navigate, backHref]);

  const goRow = useCallback(
    (id) => {
      if (!id || id === d.rowId) return;
      setRevealed(false);
      st.set({ rowId: id, step: null, trials: {} });
    },
    [d.rowId, st],
  );
  const railIds = d.sets?.[st.rail] ?? [];
  const isReviewed = (id) =>
    d.rowsState?.[id]?.viewed || d.rowsState?.[id]?.verdicts;
  const move = (dir, unreviewed) => {
    const list = railIds.includes(d.rowId) ? railIds : [d.rowId, ...railIds];
    const id = stepRow(list, d.rowId, dir, unreviewed ? isReviewed : null);
    if (id) goRow(id);
    else
      notify(
        dir > 0
          ? `That was the last ${unreviewed ? "unreviewed " : ""}task in this list`
          : "This is the first task in this list",
        { key: "tc-end" },
      );
  };

  // Jump to a row: it becomes the URL's `step`, scrolls into view and opens
  // in every lane (or just `focus`, a {laneId, n} step, when given).
  const anchor = (rowId, focus) => {
    if (!rowId) return;
    st.set({ step: rowId.startsWith("m") ? rowId : null });
    ui.openRow?.(rowId, focus);
  };
  const stepDiff = (dir) => {
    const ids = d.diffRows;
    if (!ids.length) return;
    const at = ids.indexOf(d.anchorRowId);
    const next =
      at < 0
        ? dir > 0
          ? ids[0]
          : ids[ids.length - 1]
        : ids[(at + dir + ids.length) % ids.length];
    anchor(next);
  };

  const setTrial = (laneIdx, dir) => {
    const lane = lanes[laneIdx];
    if (!lane?.trials?.length) return;
    const ns = lane.trials.map((t) => t.trial);
    const at = ns.indexOf(lane.trial?.trial);
    const n = ns[(at + dir + ns.length) % ns.length];
    st.set({ trials: { ...p.trials, [lane.id]: n } });
  };

  const makeBaseline = (laneIdx) => {
    const lane = lanes[laneIdx];
    if (!lane || lane.kind === "production" || laneIdx === 0 || d.blind) return;
    const prev = d.baselineId;
    st.set({
      baselineId: lane.id,
      runIds: [lane.id, ...d.laneIds.filter((x) => x !== lane.id)],
    });
    setFocusIdx(1);
    notify(`${lane.label} is the baseline here`, {
      key: "tc-baseline",
      undo: {
        onUndo: () =>
          st.set({
            baselineId: prev,
            runIds: [prev, ...d.laneIds.filter((x) => x !== prev)],
          }),
      },
    });
  };

  const toggleViewed = () => {
    const next = !viewed;
    setOptimistic((o) => ({
      ...o,
      [d.rowId]: { ...o[d.rowId], viewed: next },
    }));
    write({ viewed: next });
    notify(next ? `Marked ${rowLabel} viewed` : `${rowLabel} unmarked`, {
      key: "tc-viewed",
      undo: { onUndo: () => write({ viewed: !next }) },
    });
  };

  const choose = (choice) => {
    const wasViewed = viewed;
    if (lanes.length < 2 || !right) {
      notify("Pick at least 2 runs to judge which did better.", {
        key: "tc-verdict",
      });
      return;
    }
    const verdict = {
      left_run_id: left.id,
      right_run_id: right.id,
      choice,
      blind: d.blind,
    };
    setOptimistic((o) => ({
      ...o,
      [d.rowId]: {
        ...o[d.rowId],
        verdict: { ...verdict, at: String(Date.now()) },
        viewed: true,
      },
    }));
    write({ verdict });
    notify(
      verdictToast(choice, {
        leftLabel: left.blindName ?? ui.nameOf?.(left) ?? left.label,
        rightLabel: right.blindName ?? ui.nameOf?.(right) ?? right.label,
        rowLabel,
      }),
      {
        key: "tc-verdict",
        undo: {
          onUndo: () => {
            setOptimistic((o) => ({
              ...o,
              [d.rowId]: { ...o[d.rowId], verdict: null, viewed: wasViewed },
            }));
            // A verdict also ticks the task; undo puts the tick back as it was.
            write({ undo: "verdict", ...(wasViewed ? {} : { viewed: false }) });
          },
        },
      },
    );
  };
  const reason = (text) =>
    write({ verdict: { reason: text } }, () =>
      notify("Reason saved", { key: "tc-reason" }),
    );

  const addNote = (text, stepRef) =>
    write(
      {
        note: {
          text,
          ...(stepRef ? { run_id: stepRef.laneId, step_n: stepRef.stepN } : {}),
        },
      },
      () => notify(`Note added to ${rowLabel}`, { key: "tc-note" }),
    );
  const deleteNote = (n) => {
    write({ undo: `note:${n.id}` });
    notify("Note deleted", {
      key: "tc-note-del",
      undo: {
        onUndo: () =>
          write({ note: { text: n.text, run_id: n.run_id, step_n: n.step_n } }),
      },
    });
  };

  const copyLink = async () => {
    const ok = await copyText(st.linkFor(datasetId));
    notify(
      ok
        ? linkToast({ rowLabel, lanes: lanes.length })
        : "Couldn't copy the link",
      { key: "tc-link", variant: ok ? "default" : "error" },
    );
  };

  const focusSearch = () => {
    const inRail = document.activeElement?.closest?.("[data-rail]");
    const visible = (el) => Boolean(el && el.offsetParent);
    if (inRail && visible(railSearchRef.current))
      return railSearchRef.current.focus();
    setTimeout(
      () => (stepSearchRef.current ?? railSearchRef.current)?.focus(),
      60,
    );
    return undefined;
  };

  const handlers = {
    next: () => move(1),
    prev: () => move(-1),
    nextUnreviewed: () => move(1, true),
    prevUnreviewed: () => move(-1, true),
    prevCandidate: () => setFocusIdx((i) => Math.max(0, i - 1)),
    nextCandidate: () => setFocusIdx((i) => Math.min(lanes.length - 1, i + 1)),
    prevTrial: () => setTrial(focusIdx, -1),
    nextTrial: () => setTrial(focusIdx, 1),
    makeBaseline: () => makeBaseline(focusIdx),
    diffOnly: () => st.set({ diff: !p.diff }),
    nextDiff: () => stepDiff(1),
    prevDiff: () => stepDiff(-1),
    jumpFailedCheck: () => ui.jumpFirstWrong?.(),
    expandAllSteps: () => ui.expandAll?.(),
    collapseAllSteps: () => ui.collapseAll?.(),
    toggleMap: () => ui.toggleMap?.(),
    inspect: () => ui.toggleInspect?.(),
    markViewed: toggleViewed,
    addNote: () => {
      st.set({ dock: "notes" });
      setTimeout(() => noteRef.current?.focus(), 60);
    },
    copyLink,
    search: focusSearch,
    escape: () => {
      // Esc closes an open menu, popover or field first; only then goes back.
      const el = document.activeElement;
      const typing =
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.isContentEditable);
      const overlay = document.querySelector(
        ".MuiPopover-root, .MuiMenu-root, .MuiDialog-root, .MuiDrawer-root",
      );
      if (typing || overlay) return;
      // Then the Inspect panel, then open steps; only with nothing open,
      // back to the Matrix.
      if (ui.inspectOpen) {
        ui.closeInspect?.();
        return;
      }
      if (ui.anyOpen) {
        ui.collapseAll?.();
        return;
      }
      goBack();
    },
  };
  Object.entries(VERDICT_CHOICES).forEach(([action, choice]) => {
    handlers[action] = () => choose(choice);
  });
  // R4 lane T: the depth keys (G graph) and depth-specific overrides
  // (`inspect: undefined` while the Graph shows, so `I` inspects a node).
  Object.assign(handlers, ui.extraHandlers ?? {});
  useShortcuts("taskCompare", handlers, { enabled: !ui.dialogOpen });

  const focused = lanes[focusIdx] ?? null;
  useRegisterCommands(
    "task-compare",
    [
      {
        id: "tc.nextUnreviewed",
        label: "Next unreviewed task",
        shortcut: "nextUnreviewed",
        icon: "mdi:skip-next",
        run: () => move(1, true),
      },
      {
        id: "tc.makeBaseline",
        label: `Make ${focused?.label ?? "the focused lane"} baseline`,
        shortcut: "makeBaseline",
        icon: "mdi:pin-outline",
        run: () => makeBaseline(focusIdx),
        when: () => focusIdx > 0 && !d.blind,
      },
      {
        id: "tc.diff",
        label: p.diff ? "Show every step" : "Differences only",
        shortcut: "diffOnly",
        icon: "mdi:vector-difference",
        run: handlers.diffOnly,
      },
      {
        id: "tc.map",
        label: ui.mapOpen ? "Hide map" : "Show map",
        shortcut: "toggleMap",
        icon: "mdi:chart-timeline-variant",
        run: () => ui.toggleMap?.(),
      },
      {
        id: "tc.inspect",
        label: ui.inspectOpen ? "Close the Inspect panel" : "Inspect panel",
        shortcut: "inspect",
        icon: "mdi:dock-bottom",
        run: () => ui.toggleInspect?.(),
      },
      {
        id: "tc.expandAll",
        label: "Expand all steps",
        shortcut: "expandAllSteps",
        icon: "mdi:unfold-more-horizontal",
        run: () => ui.expandAll?.(),
      },
      {
        id: "tc.collapseAll",
        label: "Collapse all steps",
        shortcut: "collapseAllSteps",
        icon: "mdi:unfold-less-horizontal",
        run: () => ui.collapseAll?.(),
      },
      {
        id: "tc.firstWrong",
        label: "First wrong step",
        shortcut: "jumpFailedCheck",
        icon: "mdi:alert-circle-outline",
        run: () => ui.jumpFirstWrong?.(),
        when: () => Boolean(d.marks.firstWrongStep),
      },
      {
        id: "tc.trace",
        label: `Open ${focused?.label ?? "lane"} full trace`,
        icon: "mdi:open-in-new",
        run: () => ui.openTrace(focused?.id),
        when: () => Boolean(focused?.trial?.trace_id),
      },
      {
        id: "tc.blind",
        label: d.blind ? "Blind review off" : "Blind review on",
        icon: "mdi:eye-off-outline",
        run: () => st.set({ blind: !p.blind }),
      },
      {
        id: "tc.copyStep",
        label: "Copy link to this step",
        shortcut: "copyLink",
        icon: "mdi:link-variant",
        run: copyLink,
      },
      {
        id: "tc.viewed",
        label: viewed ? `Unmark ${rowLabel} viewed` : `Mark ${rowLabel} viewed`,
        shortcut: "markViewed",
        icon: "mdi:check-circle-outline",
        run: toggleViewed,
      },
      {
        id: "tc.note",
        label: "Add a note to this task",
        shortcut: "addNote",
        icon: "mdi:comment-text-outline",
        run: handlers.addNote,
      },
      {
        id: "tc.cards",
        label: "Switch to Cards (every run)",
        icon: "mdi:view-grid-outline",
        run: () => st.set({ runIds: d.selection }),
        when: () => d.selection.length > 4,
      },
      {
        id: "tc.keys",
        label: "Side by side: keyboard shortcuts",
        icon: "mdi:keyboard-outline",
        run: () => openShortcutSheet({ surface: "taskCompare" }),
      },
    ],
    [
      focusIdx,
      focused?.label,
      p.diff,
      p.blind,
      d.blind,
      ui.mapOpen,
      ui.inspectOpen,
      viewed,
      rowLabel,
      d.selection.length,
    ],
  );

  return {
    backHref,
    backLabel,
    goBack,
    goRow,
    anchor,
    stepDiff,
    setTrial,
    makeBaseline,
    toggleViewed,
    choose,
    reason,
    addNote,
    deleteNote,
    copyLink,
    revealed,
    setRevealed,
    noteRef,
    railSearchRef,
    stepSearchRef,
    viewed,
    current,
    left,
    right,
    saving: mutation.isPending,
  };
}
