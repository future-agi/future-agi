import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { NUM_SX } from "src/sections/common/format/fmt";
import { JsonTab, PromptsTab, ScoresTab, WorkspaceTab } from "./RawTabs";
import { ChecksMatrix, FinalAnswers } from "./DockPanels";
import LaneTimelines from "./LaneTimelines";
import ModalityLens from "src/sections/common/Lenses/ModalityLens";
import { lensKindOf } from "src/sections/common/Lenses/lensKind";
import { laneNames } from "./storyGrid";
import SetupTab from "./SetupTab";

/** Final outputs of the lanes (the trial output, else the final answer). */
const finalsOf = (lanes) =>
  lanes.map((l) => l.trial?.output ?? l.trial?.final_answer ?? null);

export function ToolCalls({ lanes, onSelectStep }) {
  const [pick, setPick] = useState(null);
  const tools = useMemo(() => {
    const names = new Set();
    lanes.forEach((l) =>
      (l.steps ?? []).forEach(
        (s) =>
          (s.kind === "tool" || s.kind === "retrieval") &&
          names.add(s.toolName ?? s.name),
      ),
    );
    return [...names];
  }, [lanes]);
  const callsOf = (l, t) =>
    (l.steps ?? []).filter(
      (s) =>
        (s.kind === "tool" || s.kind === "retrieval") &&
        (s.toolName ?? s.name) === t,
    );
  if (!tools.length)
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        No tool calls in these trials.
      </Typography>
    );
  const sel = pick
    ? {
        base: callsOf(lanes[0], pick.tool)[0],
        cur: callsOf(lanes[pick.l], pick.tool)[0],
      }
    : null;
  return (
    <Box sx={{ p: 1.5 }}>
      <Box
        component="table"
        sx={{
          borderCollapse: "collapse",
          "& td, & th": {
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1.25,
            py: 0.5,
            typography: "s2",
          },
        }}
      >
        <thead>
          <tr>
            <Box
              component="th"
              sx={{
                textAlign: "left",
                color: "text.secondary",
                fontWeight: 600,
              }}
            >
              Tool
            </Box>
            {lanes.map((l) => (
              <Box
                key={l.id}
                component="th"
                sx={{
                  color: l.color?.solid,
                  fontWeight: 700,
                  textAlign: "right",
                  whiteSpace: "nowrap",
                }}
              >
                {l.blindName ?? l.label}
              </Box>
            ))}
          </tr>
        </thead>
        <tbody>
          {tools.map((t) => (
            <tr key={t}>
              <Box component="td" sx={{ fontFamily: "monospace" }}>
                {t}
              </Box>
              {lanes.map((l, i) => {
                const calls = callsOf(l, t);
                const errors = calls.filter((s) => s.error).length;
                return (
                  <Box key={l.id} component="td" sx={{ ...NUM_SX }}>
                    {calls.length ? (
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() => {
                          setPick({ tool: t, l: i });
                          onSelectStep?.(l.id, calls[0].n);
                        }}
                        sx={{
                          minWidth: 0,
                          ...NUM_SX,
                          color: errors ? "error.main" : "text.primary",
                        }}
                      >
                        {calls.length} {calls.length === 1 ? "call" : "calls"}
                        {errors
                          ? ` · ${errors} ${errors === 1 ? "error" : "errors"}`
                          : ""}
                      </Button>
                    ) : (
                      <Typography
                        component="span"
                        typography="s2"
                        color="text.disabled"
                      >
                        none
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </tr>
          ))}
        </tbody>
      </Box>
      {sel?.cur ? (
        <Box sx={{ mt: 2, maxWidth: 900 }}>
          <Typography typography="s2" fontWeight={600}>
            {pick.tool} · {lanes[pick.l].blindName ?? lanes[pick.l].label}
            {pick.l > 0 ? ` vs ${lanes[0].blindName ?? lanes[0].label}` : ""} ·
            first call
          </Typography>
          <Typography typography="s3" color="text.secondary" sx={{ mt: 1 }}>
            Arguments
          </Typography>
          <TextDiff
            from={pick.l > 0 ? sel.base?.input ?? null : sel.cur.input}
            to={sel.cur.input}
            kind="json"
          />
          <Typography typography="s3" color="text.secondary" sx={{ mt: 1 }}>
            Result
          </Typography>
          <TextDiff
            from={pick.l > 0 ? sel.base?.output ?? null : sel.cur.output}
            to={sel.cur.output}
            kind="json"
          />
        </Box>
      ) : null}
    </Box>
  );
}
ToolCalls.propTypes = {
  lanes: PropTypes.array.isRequired,
  onSelectStep: PropTypes.func,
};

/**
 * The Inspect panel's body (ONE-VIEW-SPEC §7.3): what was Raw and the dock
 * panels, lane-parallel. Rows in Timeline / Tool calls / Prompts / Checks
 * open that step in the steps above (one selection model).
 */
export default function InspectBody({
  tab,
  graphs,
  onVote,
  lanes,
  rowChecks,
  onOpenTrace,
  onSelectStep,
  onClose,
  compact = true,
  lensHeight,
  input,
  runOf,
}) {
  switch (tab) {
    case "setup":
      return <SetupTab lanes={lanes} runOf={runOf} />;
    case "answers": {
      // Media / JSON answers compare in the modality lens (its
      // vote = the verdict bar's choice); text keeps the R3 word diff.
      const finals = finalsOf(lanes);
      const kind = lensKindOf(finals);
      if (kind && kind !== "call")
        return (
          <ModalityLens
            kind={kind}
            lanes={lanes.map((l, i) => ({
              runId: l.id,
              // Names with ids: `#2A gpt-image-1`.
              label:
                l.blindName ??
                [l.label, laneNames(lanes).get(l.id)].filter(Boolean).join(" "),
              color: l.color?.solid,
              isBaseline: i === 0,
              value: finals[i],
              trace_id: l.trial?.trace_id ?? undefined,
              // IT-R5 G4 P2: the lane's own scores (the grid's numbers).
              result: l.trial?.scores ? { scores: l.trial.scores } : undefined,
            }))}
            input={input}
            onVote={onVote}
            compact={compact}
            height={lensHeight}
          />
        );
      return <FinalAnswers lanes={lanes} />;
    }
    case "checks":
      return (
        <ChecksMatrix
          rowChecks={rowChecks ?? []}
          lanes={lanes}
          onGoToStep={onSelectStep}
        />
      );
    case "timeline":
      // Observe's SpanTreeTimeline per lane on one axis.
      return <LaneTimelines graphs={graphs ?? []} />;
    case "tools":
      return <ToolCalls lanes={lanes} onSelectStep={onSelectStep} />;
    case "prompts":
      return <PromptsTab lanes={lanes} onSelectStep={onSelectStep} />;
    case "workspace":
      return <WorkspaceTab lanes={lanes} onSteps={onClose} />;
    case "scores":
      return <ScoresTab lanes={lanes} rowChecks={rowChecks} />;
    case "json":
      return <JsonTab lanes={lanes} onOpenTrace={onOpenTrace} />;
    default:
      return null;
  }
}

InspectBody.propTypes = {
  input: PropTypes.any,
  tab: PropTypes.string,
  graphs: PropTypes.array,
  onVote: PropTypes.func,
  lanes: PropTypes.array.isRequired,
  wf: PropTypes.string,
  onWf: PropTypes.func,
  rowChecks: PropTypes.array,
  onOpenTrace: PropTypes.func,
  onSelectStep: PropTypes.func,
  onClose: PropTypes.func,
  compact: PropTypes.bool,
  lensHeight: PropTypes.number,
  runOf: PropTypes.func,
};
