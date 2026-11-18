import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useTrialRoutes } from "src/api/simulate-environments/runDetail";
import { TRIAL_ROUTES_COPY as C } from "../callTraces.constants";
import { runOutcome } from "../trace/traceTable.constants";
import { Toggle } from "./SequenceParts";

const linkSx = {
  typography: "s3",
  fontWeight: 500,
  color: "text.secondary",
  p: 0,
  border: 0,
  bgcolor: "transparent",
  cursor: "pointer",
  whiteSpace: "nowrap",
  textDecoration: "underline",
  textUnderlineOffset: "2px",
  textDecorationColor: (t) => t.palette.divider,
  "&:hover": { textDecorationColor: "currentColor" },
};

const BAD = new Set(["failed", "no_answer"]);

const labelOf = (column) =>
  column.kind === "tool_call"
    ? `${column.from_name ?? C.unknown} · ${column.tool}`
    : `${column.from_name ?? C.unknown} → ${column.to_name ?? C.unknown}`;

function Cell({ cell, kind, trialNumber, onOpen }) {
  const word = C.cell(cell.state, kind, cell.count);
  const none = cell.state === "absent" || cell.step == null;
  const bad = BAD.has(cell.state);
  return (
    <Box
      component={none ? "span" : "button"}
      type={none ? undefined : "button"}
      aria-label={none ? undefined : C.openStep(trialNumber)}
      onClick={none ? undefined : () => onOpen(cell.step)}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        height: 20,
        px: 0.75,
        border: 0,
        borderRadius: "10px",
        typography: "s3",
        fontWeight: 500,
        whiteSpace: "nowrap",
        cursor: none ? "default" : "pointer",
        bgcolor: (t) =>
          none
            ? "transparent"
            : bad
              ? alpha(t.palette.error.main, 0.1)
              : t.palette.background.neutral,
        color: none ? "text.disabled" : bad ? "error.main" : "text.secondary",
      }}
    >
      {word}
    </Box>
  );
}

Cell.propTypes = {
  cell: PropTypes.object.isRequired,
  kind: PropTypes.string.isRequired,
  trialNumber: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
};

/**
 * Every trial of the open call's scenario on one line: where they first went
 * differently, and the table on demand. Each cell opens its trial at that step.
 */
export default function TrialRoutes({
  testExecutionId,
  scenarioKey,
  currentId,
  onOpen,
}) {
  const [open, setOpen] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const { data, isPlaceholderData } = useTrialRoutes(
    testExecutionId,
    scenarioKey,
    showTools,
  );
  if (!data || data.trials.length < 2) return null;
  const { columns, trials, split } = data;
  const tint = (t) => alpha(t.palette.warning.main, 0.08);
  const splitColumn = split ? columns[split.column] : null;
  const splitWords = split
    ? split.groups
        .map((group) =>
          C.splitGroup(
            C.cell(group.state, splitColumn.kind, group.count),
            group.trials,
          ),
        )
        .join(" · ")
    : "";

  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", pt: 0.75 }}>
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {C.trials(trials.length)}
        </Typography>
        <Typography
          data-testid="trial-routes-split"
          sx={{
            typography: "s3",
            color: "text.secondary",
            flex: 1,
            minWidth: 200,
          }}
        >
          {split ? (
            <>
              {C.splitAt}
              <Box
                component="span"
                sx={{ fontWeight: 600, color: "text.primary" }}
              >
                {labelOf(splitColumn)}
              </Box>
              {`: ${splitWords}`}
            </>
          ) : (
            C.sameRoute
          )}
        </Typography>
        <Typography
          component="button"
          type="button"
          onClick={() => setOpen((was) => !was)}
          sx={linkSx}
        >
          {open ? C.hide : C.show}
        </Typography>
      </Stack>
      {/* Slides open, so the diagram below is seen moving down. */}
      <Collapse in={open} unmountOnExit>
        <Stack direction="row" justifyContent="flex-end" sx={{ py: 0.5 }}>
          <Toggle on={showTools} onClick={() => setShowTools((was) => !was)}>
            {C.showTools}
          </Toggle>
        </Stack>
        {/* Dimmed, not emptied, while the tool-call columns load. */}
        <Box
          sx={{
            overflowX: "auto",
            pb: 1,
            opacity: isPlaceholderData ? 0.5 : 1,
            transition: "opacity 150ms",
          }}
        >
          <Box
            component="table"
            sx={{
              borderCollapse: "collapse",
              minWidth: "100%",
              "& th, & td": {
                px: 0.75,
                py: 0.625,
                borderBottom: "1px solid",
                borderColor: "divider",
                textAlign: "left",
                whiteSpace: "nowrap",
              },
              "& th": {
                typography: "s3",
                fontSize: 10.5,
                fontWeight: 600,
                color: "text.disabled",
                letterSpacing: "0.03em",
                textTransform: "uppercase",
                maxWidth: 150,
                overflow: "hidden",
                textOverflow: "ellipsis",
              },
            }}
          >
            <thead>
              <tr>
                <th>{C.trial}</th>
                <th>{C.result}</th>
                {columns.map((column) => (
                  <Box
                    component="th"
                    key={column.key}
                    title={labelOf(column)}
                    sx={
                      column.key === split?.key ? { bgcolor: tint } : undefined
                    }
                  >
                    {labelOf(column)}
                  </Box>
                ))}
              </tr>
            </thead>
            <tbody>
              {trials.map((one) => {
                // Named and coloured as the run's calls table names a call's outcome.
                const { label, color } = runOutcome(one.result);
                const isCurrent = one.call_execution_id === currentId;
                return (
                  <tr key={one.call_execution_id}>
                    <td>
                      <Box
                        component="button"
                        type="button"
                        onClick={() => onOpen(one.call_execution_id, null)}
                        sx={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 0.75,
                          p: 0,
                          border: 0,
                          bgcolor: "transparent",
                          cursor: "pointer",
                          typography: "s2",
                          fontWeight: isCurrent ? 600 : 400,
                          color: isCurrent ? "text.primary" : "text.secondary",
                        }}
                      >
                        <Box
                          sx={{
                            width: 7,
                            height: 7,
                            borderRadius: "50%",
                            bgcolor: color,
                          }}
                        />
                        {C.trialN(one.number)}
                      </Box>
                    </td>
                    <td>
                      <Typography
                        component="span"
                        sx={{ typography: "s3", fontWeight: 600, color }}
                      >
                        {label}
                      </Typography>
                    </td>
                    {one.cells ? (
                      columns.map((column, index) => (
                        <Box
                          component="td"
                          key={column.key}
                          sx={
                            column.key === split?.key
                              ? { bgcolor: tint }
                              : undefined
                          }
                        >
                          <Cell
                            cell={one.cells[index]}
                            kind={column.kind}
                            trialNumber={one.number}
                            onOpen={(step) =>
                              onOpen(one.call_execution_id, step)
                            }
                          />
                        </Box>
                      ))
                    ) : (
                      <td colSpan={columns.length}>
                        <Typography
                          component="span"
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {C.notCompared[one.route] ?? one.route}
                        </Typography>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
}

TrialRoutes.propTypes = {
  testExecutionId: PropTypes.string,
  scenarioKey: PropTypes.string,
  // The open call: its row reads in bold.
  currentId: PropTypes.string,
  // (callExecutionId, stepKey | null) → that trial, on that step of its record.
  onOpen: PropTypes.func.isRequired,
};
