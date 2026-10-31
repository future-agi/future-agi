import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useCallDetail } from "src/api/simulate-environments/runDetail";
import SideDrawer from "../../components/SideDrawer";
import SoonChip from "../../components/SoonChip";
import { RunLetter } from "./RunPills";
import { VerdictChip } from "./CompareTable";

const ROLE_LABEL = { agent: "Agent", customer: "Caller", tool: "Tool" };

function Transcript({ callId }) {
  const { callDetail, isLoading } = useCallDetail(callId);
  if (isLoading) {
    return (
      <Box sx={{ py: 4, display: "grid", placeItems: "center" }}>
        <CircularProgress size={18} />
      </Box>
    );
  }
  const turns = callDetail?.turns ?? [];
  if (!turns.length) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        This call has no transcript.
      </Typography>
    );
  }
  return (
    <Stack spacing={1.25}>
      {turns.map((turn, i) => (
        <Box key={i}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: turn.role === "agent" ? "primary.main" : "text.subtitle",
            }}
          >
            {ROLE_LABEL[turn.role] || turn.role}
          </Typography>
          <Typography sx={{ typography: "s2", whiteSpace: "pre-wrap" }}>
            {turn.text}
          </Typography>
        </Box>
      ))}
    </Stack>
  );
}
Transcript.propTypes = { callId: PropTypes.string };

function Checklist({ call }) {
  const steps = call?.subGoalResults ?? [];
  if (!steps.length) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        This call has no sub-goals.
      </Typography>
    );
  }
  const met = steps.filter((s) => s.passed).length;
  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
        {`${met} of ${steps.length} met`}
      </Typography>
      {steps.map((step, i) => (
        <Stack key={`${step.name}-${i}`} direction="row" spacing={1}>
          <Iconify
            icon={
              step.passed
                ? "solar:check-circle-bold"
                : step.passed === false
                  ? "solar:close-circle-bold"
                  : "solar:minus-circle-linear"
            }
            width={16}
            sx={{
              mt: 0.25,
              flexShrink: 0,
              color: step.passed
                ? "success.main"
                : step.passed === false
                  ? "error.main"
                  : "text.disabled",
            }}
          />
          <Box minWidth={0}>
            <Typography sx={{ typography: "s2" }}>{step.name}</Typography>
            {step.reason && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {step.reason}
              </Typography>
            )}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
Checklist.propTypes = { call: PropTypes.object };

// One run's side of the scenario: its verdict, the trial being read, and that
// call's transcript or checklist.
function RunColumn({ run, trials, verdict, tab }) {
  const [index, setIndex] = useState(0);
  const call = trials[Math.min(index, trials.length - 1)] ?? null;
  return (
    <Stack
      sx={{
        minWidth: 280,
        flex: 1,
        borderLeft: "1px solid",
        borderColor: "divider",
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <RunLetter letter={run.letter} color={run.color} />
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", flex: 1 }}
        >
          {run.label}
        </Typography>
        {trials.length > 1 && (
          <TextField
            select
            size="small"
            value={Math.min(index, trials.length - 1)}
            onChange={(e) => setIndex(Number(e.target.value))}
            inputProps={{ "aria-label": `${run.label} trial` }}
            sx={{ "& .MuiInputBase-input": { typography: "s3", py: 0.5 } }}
          >
            {trials.map((t, i) => (
              <MenuItem key={t.id} value={i} sx={{ typography: "s3" }}>
                {`Trial ${t.trialIndex ?? i + 1}`}
              </MenuItem>
            ))}
          </TextField>
        )}
        <VerdictChip verdict={verdict} />
      </Stack>
      <Box sx={{ p: 2, overflowY: "auto", flex: 1, minHeight: 0 }}>
        {!call ? (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            This scenario did not run here.
          </Typography>
        ) : tab === "checklist" ? (
          <Checklist call={call} />
        ) : (
          <Transcript callId={call.id} />
        )}
      </Box>
    </Stack>
  );
}
RunColumn.propTypes = {
  run: PropTypes.object.isRequired,
  trials: PropTypes.array.isRequired,
  verdict: PropTypes.object.isRequired,
  tab: PropTypes.string.isRequired,
};

/**
 * A scenario opened from the comparison: the call in every run, side by
 * side, with its transcript or its sub-goal checklist. The call graph isn't
 * in the API yet.
 */
export default function CallCompareDrawer({
  row,
  rows,
  runs,
  onOpenRow,
  onClose,
}) {
  const [tab, setTab] = useState("transcript");
  const index = row ? rows.findIndex((r) => r.id === row.id) : -1;
  const step = (by) => {
    const next = rows[index + by];
    if (next) onOpenRow(next);
  };

  return (
    <SideDrawer open={!!row} onClose={onClose} width="calc(100vw - 220px)">
      {row && (
        <Stack data-testid="call-compare" sx={{ height: "100%", minHeight: 0 }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              pl: 2,
              pr: 6,
              py: 1.25,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s1",
                fontWeight: "fontWeightSemiBold",
                flex: 1,
              }}
            >
              {row.title}
            </Typography>
            <CustomTooltip show arrow size="small" title="Previous scenario">
              <span>
                <IconButton
                  size="small"
                  aria-label="Previous scenario"
                  disabled={index <= 0}
                  onClick={() => step(-1)}
                >
                  <Iconify icon="solar:alt-arrow-up-linear" width={16} />
                </IconButton>
              </span>
            </CustomTooltip>
            <CustomTooltip show arrow size="small" title="Next scenario">
              <span>
                <IconButton
                  size="small"
                  aria-label="Next scenario"
                  disabled={index < 0 || index >= rows.length - 1}
                  onClick={() => step(1)}
                >
                  <Iconify icon="solar:alt-arrow-down-linear" width={16} />
                </IconButton>
              </span>
            </CustomTooltip>
          </Stack>

          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <Typography
              sx={{
                typography: "s3",
                fontWeight: "fontWeightSemiBold",
                color: "text.subtitle",
              }}
            >
              {`Comparing ${runs.length} runs`}
            </Typography>
            <Tabs
              value={tab}
              onChange={(_, v) => setTab(v)}
              sx={{ minHeight: 40 }}
            >
              <Tab
                value="transcript"
                label="Transcript"
                sx={{ minHeight: 40 }}
              />
              <Tab value="checklist" label="Checklist" sx={{ minHeight: 40 }} />
            </Tabs>
            <SoonChip label="Graph" what="The call graph" size="tiny" />
            <Box flex={1} />
            <SoonChip
              label="Show diff"
              what="A diff of what each run did differently"
              size="tiny"
            />
          </Stack>

          <Stack
            direction="row"
            sx={{ flex: 1, minHeight: 0, overflowX: "auto" }}
          >
            {runs.map((run) => (
              <RunColumn
                key={`${row.id}-${run.id}`}
                run={run}
                trials={row.cells[run.id]}
                verdict={row.verdicts[run.id]}
                tab={tab}
              />
            ))}
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}
CallCompareDrawer.propTypes = {
  row: PropTypes.object,
  rows: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  onOpenRow: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
