import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Chip, CircularProgress, Stack, Typography, IconButton, Tooltip, Tab } from "@mui/material";

import Iconify from "src/components/iconify";
import EvalsTabView from "src/components/traceDetail/EvalsTabView";
import { CustomTabs } from "src/components/tabs/tabs";
import { useCallDetail } from "src/api/simulate-environments/runDetail";

import { BUILD_TONES } from "../../../buildEnvironment/buildTones";
import ComingSoonChip from "../../../components/ComingSoonChip";
import EmptyState from "../../../components/EmptyState";
import ChatTranscriptPane from "./ChatTranscriptPane";
import { Meta, Cell, Attr } from "./chatDrawerCells";

const num = (n) => (n == null ? "-" : Number(n).toLocaleString());
const secs = (ms) => (ms == null ? "-" : `${(ms / 1000).toFixed(1)}s`);

// This eval was removed from the environment after it graded this call.
// The verdict stands as it was stored — it is marked, never hidden and
// never restated — everywhere a call's verdicts render, so the banner and
// the Evals tab row share this exact chip.
function RemovedChip() {
  return (
    <Chip
      data-testid="removed-eval-marker"
      size="small"
      label="Removed"
      sx={{
        height: 18,
        fontSize: 10,
        fontWeight: 600,
        bgcolor: "background.neutral",
        color: "text.subtitle",
        "& .MuiChip-label": { px: 0.75 },
      }}
    />
  );
}

// The chat / non-voice call drawer — a lean port of the designer's two-pane
// CallDrawer over REAL call-detail data. The left pane is the transcript (with
// tool calls inline); the right pane is the measurement — meta chips, the
// failed-eval banner and the Analytics / Evals / Messages / Attributes tabs.
// Checklist and Graph have no real endpoint yet, so they are deferred seams.
// Prev/next call, the same controls and ↑/↓ keys as the voice drawer header.
function NavArrow({ icon, label, onClick, disabled }) {
  return (
    <Tooltip arrow title={label}>
      <span>
        <IconButton
          size="small"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: "2px", p: 0.25 }}
        >
          <Iconify icon={icon} width={16} />
        </IconButton>
      </span>
    </Tooltip>
  );
}
NavArrow.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

// A scroll pane keeps ↑/↓ for itself while focused, so the arrows scroll it
// instead of stepping to another conversation — the voice transcript does the
// same. Focusable so a click inside hands it the keyboard; default not
// prevented, so the browser still scrolls.
const ownsArrowKeys = {
  tabIndex: 0,
  onKeyDown: (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") e.stopPropagation();
  },
};

export default function ChatCallDrawer({
  task,
  onClose,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
}) {
  const [pane, setPane] = useState("transcript");
  useEffect(() => {
    const onKeyDown = (e) => {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      if (e.key === "ArrowDown" && hasNext) {
        e.preventDefault();
        onNext?.();
      } else if (e.key === "ArrowUp" && hasPrev) {
        e.preventDefault();
        onPrev?.();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [hasNext, hasPrev, onNext, onPrev]);
  const [side, setSide] = useState("analytics");
  const { callDetail, isLoading } = useCallDetail(task.id);

  // The header and chips paint from the already-loaded row immediately, then
  // hydrate from the call detail once it resolves.
  const turns = callDetail?.turns || [];
  const stats = callDetail?.stats || {};
  const evalResults = callDetail?.evalResults ?? task.evalResults ?? [];
  const failed = evalResults.filter((r) => r.passed === false);
  const drawerEvals = evalResults.map((result) => ({
    ...result,
    eval_name: result.name,
    score: result.score == null ? null : Math.round(result.score * 100),
    score_label:
      result.output_type === "Pass/Fail" && result.passed != null
        ? result.passed
          ? "Passed"
          : "Failed"
        : undefined,
    explanation: result.reason,
  }));
  const durationMs = callDetail?.durationS != null ? callDetail.durationS * 1000 : task.durationMs;
  const turnCount = stats.turnCount ?? task.turns;
  const tokens = callDetail?.tokens ?? task.tokens;
  const talkRatio =
    stats.aiPct != null && stats.userPct != null ? `${stats.aiPct}/${stats.userPct}` : "-";

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          Conversation ID :{" "}
          <Box component="span" sx={{ color: "text.primary" }}>
            {task.id}
          </Box>
        </Typography>
        <Tooltip arrow title="Copy conversation id">
          <IconButton size="small" onClick={() => navigator.clipboard?.writeText(task.id)}>
            <Iconify icon="solar:copy-linear" width={14} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Tooltip>
        <Stack direction="row" spacing={0.5}>
          <NavArrow icon="mdi:chevron-up" label="Previous conversation (↑)" onClick={onPrev} disabled={!hasPrev} />
          <NavArrow icon="mdi:chevron-down" label="Next conversation (↓)" onClick={onNext} disabled={!hasNext} />
        </Stack>
        <Box flex={1} />
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="mingcute:close-line" width={16} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>

      <Stack direction={{ xs: "column", md: "row" }} sx={{ flex: 1, minHeight: 0 }}>
        {/* left: the artifact */}
        <Stack sx={{ flex: 1.15, minWidth: 0, borderRight: { md: "1px solid" }, borderColor: { md: "divider" } }}>
          <CustomTabs
            value={pane}
            onChange={(_, v) => setPane(v)}
            sx={{ px: 1, borderBottom: "1px solid", borderColor: "divider", minHeight: 40 }}
          >
            <Tab value="transcript" label="Transcript" sx={{ minHeight: 40 }} />
            <Tab value="checklist" label="Checklist" sx={{ minHeight: 40 }} />
            <Tab value="graph" label="Graph" sx={{ minHeight: 40 }} />
          </CustomTabs>

          <Box {...ownsArrowKeys} sx={{ flex: 1, minHeight: 0, overflow: "auto", outline: "none" }}>
            {pane === "transcript" &&
              (isLoading && turns.length === 0 ? (
                <Stack alignItems="center" sx={{ py: 6 }}>
                  <CircularProgress size={20} />
                </Stack>
              ) : (
                <ChatTranscriptPane turns={turns} />
              ))}
            {pane === "checklist" && <DeferredPane label="Sub-goal checklist" />}
            {pane === "graph" && <DeferredPane label="Conversation graph" />}
          </Box>
        </Stack>

        {/* right: the measurement */}
        <Stack sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ px: 2, pt: 1.75, pb: 1.25 }}>
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              <Meta label="Type" value="Chat" />
              <Meta label="Status" value={task.status === "passed" ? "completed" : task.status} />
              <Meta label="Duration" value={secs(durationMs)} />
              <Meta label="Turns" value={turnCount ?? "-"} />
              <Meta label="Provider" value={callDetail?.provider || task.provider || "-"} />
            </Stack>
          </Box>

          {failed.length > 0 && (
            <Stack
              sx={{
                mx: 2,
                mb: 1.5,
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: alpha(BUILD_TONES.red, 0.35),
                bgcolor: (t) => alpha(BUILD_TONES.red, t.palette.mode === "dark" ? 0.1 : 0.05),
              }}
              spacing={1}
            >
              {failed.map((r) => (
                <Stack key={r.id} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon="solar:close-circle-bold" width={15} sx={{ color: BUILD_TONES.red, flexShrink: 0, mt: "1px" }} />
                  <Box minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                        {r.name} failed{r.score != null ? ` (${Math.round(r.score * 100)})` : ""}
                      </Typography>
                      {r.removed && <RemovedChip />}
                    </Stack>
                    {r.reason && (
                      <Typography sx={{ typography: "s2", color: "text.secondary" }}>{r.reason}</Typography>
                    )}
                  </Box>
                </Stack>
              ))}
            </Stack>
          )}

          <CustomTabs
            value={side}
            onChange={(_, v) => setSide(v)}
            variant="scrollable"
            scrollButtons={false}
            sx={{ px: 1, borderBottom: "1px solid", borderColor: "divider", minHeight: 40 }}
          >
            <Tab value="analytics" label="Chat analytics" sx={{ minHeight: 40 }} />
            <Tab value="evals" label={`Evals (${evalResults.length})`} sx={{ minHeight: 40 }} />
            <Tab value="messages" label="Messages" sx={{ minHeight: 40 }} />
            <Tab value="attributes" label="Attributes" sx={{ minHeight: 40 }} />
          </CustomTabs>

          <Box {...ownsArrowKeys} sx={{ flex: 1, minHeight: 0, overflow: "auto", outline: "none" }}>
            {side === "analytics" && (
              <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
                <Cell label="Duration" value={secs(durationMs)} />
                <Cell label="Turns" value={turnCount ?? "-"} />
                <Cell label="Words" value={num(stats.words)} />
                <Cell label="Latency" value={stats.latencyMs != null ? `${stats.latencyMs}ms` : "-"} />
                <Cell label="Talk ratio" value={talkRatio} />
                <Cell label="Tool calls" value={num(stats.toolCalls)} />
                <Cell label="Tokens" value={num(tokens)} />
              </Box>
            )}

            {side === "evals" && (
              <EvalsTabView
                evals={drawerEvals}
                emptyMessage="No evaluations ran on this call."
                showSpanColumn={false}
                showFixWithFalcon={false}
                showAddEvals={false}
              />
            )}

            {side === "messages" && (
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 2,
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "text.secondary",
                  whiteSpace: "pre-wrap",
                }}
              >
                {JSON.stringify(turns.map((s) => ({ role: s.role, content: s.text })), null, 2)}
              </Box>
            )}

            {side === "attributes" && (
              <Stack>
                <Attr label="scenario" value={task.scenario} />
                <Attr label="persona" value={task.persona} />
                <Attr label="status" value={task.status} />
              </Stack>
            )}
          </Box>
        </Stack>
      </Stack>
    </Stack>
  );
}
ChatCallDrawer.propTypes = {
  task: PropTypes.shape({
    id: PropTypes.string,
    scenario: PropTypes.string,
    persona: PropTypes.string,
    status: PropTypes.string,
    turns: PropTypes.number,
    tokens: PropTypes.number,
    durationMs: PropTypes.number,
    provider: PropTypes.string,
    evalResults: PropTypes.array,
  }).isRequired,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
};

// A deferred left-pane tab: the checklist and graph have no real feed yet, so
// this is an honest "coming soon" seam rather than mock-fed content.
function DeferredPane({ label }) {
  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{label}</Typography>
        <ComingSoonChip />
      </Stack>
      <EmptyState
        icon="solar:checklist-minimalistic-linear"
        title="Not available yet"
        body="This view needs a backend feed that isn't wired yet. The transcript, analytics and evals are live."
      />
    </Box>
  );
}
DeferredPane.propTypes = { label: PropTypes.string };
