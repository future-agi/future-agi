import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CopyButton from "src/sections/common/ux/CopyButton";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import CodeBlock from "src/sections/common/Values/CodeBlock";
import { fmtMs, fmtScore, NUM_SX } from "src/sections/common/format/fmt";
import { laneTemplate } from "./LanesGrid";
import { modelText } from "./workspaceModel";

const LLM = new Set(["think", "reply"]);
const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};
const asText = (v) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));

function Col({ lane, i, children }) {
  return (
    <Box
      sx={{
        minWidth: 0,
        p: 1.25,
        borderLeft: i ? "1px solid" : 0,
        borderColor: "divider",
      }}
    >
      <Typography
        typography="s2"
        fontWeight={700}
        sx={{ color: lane.color?.solid, mb: 0.75 }}
      >
        {lane.blindName ?? lane.label}
      </Typography>
      {children}
    </Box>
  );
}
Col.propTypes = {
  lane: PropTypes.object,
  i: PropTypes.number,
  children: PropTypes.node,
};

// ------------------------------------------------------------- prompts

/** Pick an LLM step of the baseline → per-lane system prompt, input, output. */
export function PromptsTab({ lanes, onSelectStep }) {
  const llmOf = (l) =>
    (l.rawSteps ?? []).filter((s) => s.model || LLM.has(l.stepKindOf?.(s.n)));
  const baseCalls = llmOf(lanes[0]);
  const [idx, setIdx] = useState(0);
  const pick = Math.min(idx, Math.max(0, baseCalls.length - 1));
  if (!lanes.some((l) => llmOf(l).length))
    return (
      <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
        No model calls in these trials.
      </Typography>
    );
  const baseStep = baseCalls[pick];
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography typography="s2" color="text.secondary">
          Model call
        </Typography>
        <Select
          size="small"
          value={pick}
          onChange={(e) => setIdx(Number(e.target.value))}
          sx={{
            typography: "s2",
            minWidth: 260,
            "& .MuiSelect-select": { py: 0.5 },
          }}
        >
          {baseCalls.map((s, i) => (
            <MenuItem key={s.n} value={i} sx={{ typography: "s2" }}>
              {lanes[0].blindName ?? lanes[0].label} step {s.n} · {s.name}
              {modelText(s.model) ? ` · ${modelText(s.model)}` : ""}
            </MenuItem>
          ))}
        </Select>
        <Typography typography="s3" color="text.secondary">
          Other lanes show their model call at the same position.
        </Typography>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: laneTemplate(lanes.length),
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {lanes.map((l, i) => {
          const calls = llmOf(l);
          const s = calls[Math.min(pick, calls.length - 1)];
          if (!s)
            return (
              <Col key={l.id} lane={l} i={i}>
                <Typography typography="s2" color="text.disabled">
                  No model call in this lane
                </Typography>
              </Col>
            );
          const system =
            s.prompt ??
            (Array.isArray(parse(s.input))
              ? parse(s.input).find((m) => m.role === "system")?.content
              : null);
          return (
            <Col key={l.id} lane={l} i={i}>
              <Typography typography="s3" color="text.secondary">
                {onSelectStep ? (
                  <Box
                    component="button"
                    type="button"
                    onClick={() => onSelectStep(l.id, s.n)}
                    sx={{
                      all: "unset",
                      cursor: "pointer",
                      color: "primary.main",
                      "&:hover": { textDecoration: "underline" },
                      "&:focus-visible": { outline: "2px solid" },
                    }}
                  >
                    Step {s.n} · {s.name}
                  </Box>
                ) : (
                  `Step ${s.n} · ${s.name}`
                )}
                {modelText(s.model) ? ` · ${modelText(s.model)}` : ""}
                {Number.isFinite(s.ms) ? ` · ${fmtMs(s.ms)}` : ""}
              </Typography>
              <Typography typography="s3" color="text.secondary" sx={{ mt: 1 }}>
                System prompt
              </Typography>
              {system ? (
                <Typography
                  typography="s2"
                  sx={{
                    fontFamily: "monospace",
                    whiteSpace: "pre-wrap",
                    p: 0.75,
                    bgcolor: "background.neutral",
                    borderRadius: 0.5,
                  }}
                >
                  {system}
                </Typography>
              ) : (
                <Typography typography="s2" color="text.disabled">
                  Not recorded
                </Typography>
              )}
              <Typography typography="s3" color="text.secondary" sx={{ mt: 1 }}>
                Input
              </Typography>
              <CodeBlock
                text={asText(parse(s.input) ?? "")}
                language={typeof parse(s.input) === "string" ? "text" : "json"}
                maxHeight={200}
                wrap
              />
              <Typography typography="s3" color="text.secondary" sx={{ mt: 1 }}>
                Output{i > 0 && baseStep ? " · words vs baseline" : ""}
              </Typography>
              {i > 0 && baseStep ? (
                <TextDiff
                  from={asText(parse(baseStep.output))}
                  to={asText(parse(s.output))}
                  kind="words"
                />
              ) : (
                <CodeBlock
                  text={asText(parse(s.output) ?? "")}
                  language={
                    typeof parse(s.output) === "string" ? "text" : "json"
                  }
                  maxHeight={200}
                  wrap
                />
              )}
            </Col>
          );
        })}
      </Box>
    </Box>
  );
}
PromptsTab.propTypes = {
  lanes: PropTypes.array.isRequired,
  onSelectStep: PropTypes.func,
};

// ----------------------------------------------------------- workspace

const KIND_ICON = {
  customer: "mdi:account-voice",
  assistant: "mdi:robot-outline",
  tool: "mdi:wrench-outline",
  command: "mdi:console",
};
const show = (v) =>
  v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);

function Transcript({ lines }) {
  const [q, setQ] = useState("");
  const shown = useMemo(
    () =>
      lines.filter((x) => !q || x.text.toLowerCase().includes(q.toLowerCase())),
    [lines, q],
  );
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 0.75,
          height: 26,
          mb: 0.75,
        }}
      >
        <Iconify
          icon="mdi:magnify"
          width={14}
          sx={{ color: "text.disabled" }}
        />
        <InputBase
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) =>
            e.key === "Escape" &&
            (e.stopPropagation(), setQ(""), e.currentTarget.blur())
          }
          placeholder="Search the transcript"
          inputProps={{ "aria-label": "Search the transcript" }}
          sx={{ ml: 0.5, typography: "s3", flex: 1 }}
        />
        <Typography typography="s3" color="text.disabled" sx={NUM_SX}>
          {shown.length}/{lines.length}
        </Typography>
      </Stack>
      <Box
        sx={{
          fontFamily: "monospace",
          typography: "s3",
          bgcolor: (t) => (t.palette.mode === "dark" ? "#0B1220" : "#0F172A"),
          color: "#E2E8F0",
          borderRadius: 1,
          p: 1,
          maxHeight: 320,
          overflow: "auto",
        }}
      >
        {shown.map((x, i) => (
          <Stack key={i} direction="row" spacing={1} sx={{ py: 0.125 }}>
            <Box
              component="span"
              sx={{
                color: "#94A3B8",
                flexShrink: 0,
                width: 56,
                textAlign: "right",
              }}
            >
              +{fmtMs(x.at_ms)}
            </Box>
            <Iconify
              icon={KIND_ICON[x.kind] ?? "mdi:circle-small"}
              width={13}
              sx={{
                flexShrink: 0,
                mt: "2px",
                color: x.kind === "command" ? "#FBBF24" : "#94A3B8",
              }}
            />
            <Box
              component="span"
              sx={{
                wordBreak: "break-word",
                color:
                  x.status === "error"
                    ? "#FCA5A5"
                    : x.kind === "customer"
                      ? "#93C5FD"
                      : "inherit",
              }}
            >
              {x.kind === "command" ? "$ " : ""}
              {x.text}
              {Number.isFinite(x.exit_code) ? (
                <Box
                  component="span"
                  sx={{ ml: 1, color: x.exit_code ? "#FCA5A5" : "#86EFAC" }}
                >
                  exit {x.exit_code}
                </Box>
              ) : null}
            </Box>
          </Stack>
        ))}
      </Box>
    </Box>
  );
}
Transcript.propTypes = { lines: PropTypes.array.isRequired };

/** Code / harness lanes: end state vs expected (✓/✕ per row) + transcript. */
export function WorkspaceTab({ lanes, onSteps }) {
  return (
    <Box
      sx={{ display: "grid", gridTemplateColumns: laneTemplate(lanes.length) }}
    >
      {lanes.map((l, i) => {
        const ws = l.trial?.workspace;
        return (
          <Col key={l.id} lane={l} i={i}>
            {!ws ? (
              <Box>
                <Typography typography="s2" color="text.secondary">
                  This run has no sandbox workspace; its steps are above.
                </Typography>
                <Button size="small" sx={{ mt: 0.5, px: 0 }} onClick={onSteps}>
                  Back to the steps
                </Button>
              </Box>
            ) : (
              <>
                <Typography typography="s3" color="text.secondary">
                  End state vs expected ·{" "}
                  {ws.end_state_summary ?? `${ws.end_state.length} changes`}
                </Typography>
                <Box
                  component="table"
                  sx={{
                    width: "100%",
                    borderCollapse: "collapse",
                    mt: 0.5,
                    mb: 1.25,
                    "& td, & th": {
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      px: 0.75,
                      py: 0.5,
                      typography: "s3",
                      verticalAlign: "top",
                    },
                  }}
                >
                  <thead>
                    <tr>
                      <Box
                        component="th"
                        sx={{ textAlign: "left", color: "text.secondary" }}
                      >
                        Change
                      </Box>
                      <Box
                        component="th"
                        sx={{ textAlign: "left", color: "text.secondary" }}
                      >
                        Expected
                      </Box>
                      <Box component="th" sx={{ width: 24 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {ws.end_state.map((r) => (
                      <tr key={r.path}>
                        <Box
                          component="td"
                          sx={{
                            fontFamily: "monospace",
                            wordBreak: "break-word",
                          }}
                        >
                          {r.change}
                        </Box>
                        <Box
                          component="td"
                          sx={{
                            fontFamily: "monospace",
                            color: "text.secondary",
                            wordBreak: "break-word",
                          }}
                        >
                          {"expected" in r ? show(r.expected) : "—"}
                        </Box>
                        <Box component="td">
                          <CustomTooltip
                            describeChild
                            show
                            size="small"
                            title={
                              r.ok === false
                                ? "Differs from what the task expects"
                                : r.ok
                                  ? "Matches what the task expects"
                                  : "No expectation for this change"
                            }
                          >
                            <Box
                              component="span"
                              sx={{
                                fontWeight: 700,
                                color:
                                  r.ok === false
                                    ? "error.main"
                                    : r.ok
                                      ? "success.main"
                                      : "text.disabled",
                              }}
                            >
                              {r.ok === false ? "✕" : r.ok ? "✓" : "–"}
                            </Box>
                          </CustomTooltip>
                        </Box>
                      </tr>
                    ))}
                    {!ws.end_state.length ? (
                      <tr>
                        <Box
                          component="td"
                          colSpan={3}
                          sx={{ color: "text.secondary" }}
                        >
                          {ws.failure ?? "No changes in the sandbox"}
                        </Box>
                      </tr>
                    ) : null}
                  </tbody>
                </Box>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ mb: 0.5 }}
                >
                  Transcript
                  {ws.container?.image ? ` · ${ws.container.image}` : ""}
                </Typography>
                <Transcript lines={ws.transcript ?? []} />
              </>
            )}
          </Col>
        );
      })}
    </Box>
  );
}
WorkspaceTab.propTypes = {
  lanes: PropTypes.array.isRequired,
  onSteps: PropTypes.func,
};

// --------------------------------------------------------------- scores

/** Each check and judge per lane with evidence, reasoning and judge model. */
export function ScoresTab({ lanes, rowChecks = [] }) {
  return (
    <Box
      sx={{ display: "grid", gridTemplateColumns: laneTemplate(lanes.length) }}
    >
      {lanes.map((l, i) => (
        <Col key={l.id} lane={l} i={i}>
          {!(l.trial?.checks ?? []).length ? (
            <Typography typography="s2" color="text.disabled">
              {l.kind === "production"
                ? "Production traces have no checks"
                : "Not scored"}
            </Typography>
          ) : null}
          <Stack spacing={1}>
            {(l.trial?.checks ?? []).map((c) => {
              const rc = rowChecks.find((x) => x.key === c.key);
              return (
                <Box
                  key={c.key}
                  sx={{
                    pb: 0.75,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Stack direction="row" spacing={0.75} alignItems="baseline">
                    <Box
                      component="span"
                      sx={{
                        fontWeight: 700,
                        color:
                          c.passed === false
                            ? "error.main"
                            : c.passed
                              ? "success.main"
                              : "text.disabled",
                      }}
                    >
                      {c.passed === false ? "✕" : c.passed ? "✓" : "–"}
                    </Box>
                    <Typography typography="s2" fontWeight={600}>
                      {rc?.name ?? c.key}
                    </Typography>
                    {Number.isFinite(c.score) ? (
                      <Typography
                        typography="s3"
                        color="text.secondary"
                        sx={NUM_SX}
                      >
                        score {fmtScore(c.score)}
                      </Typography>
                    ) : null}
                  </Stack>
                  {c.evidence ? (
                    <Typography typography="s3" sx={{ mt: 0.25 }}>
                      {c.evidence}
                    </Typography>
                  ) : null}
                  {c.reason && c.reason !== c.evidence ? (
                    <Typography typography="s3" color="text.secondary">
                      {c.reason}
                    </Typography>
                  ) : null}
                  {c.judge_model ? (
                    <Typography typography="s3" color="text.secondary">
                      Judge: {c.judge_model}
                    </Typography>
                  ) : null}
                  {Number.isFinite(c.cause_step_n) ? (
                    <Typography typography="s3" color="text.secondary">
                      Caused at step {c.cause_step_n}
                    </Typography>
                  ) : null}
                </Box>
              );
            })}
          </Stack>
        </Col>
      ))}
    </Box>
  );
}
ScoresTab.propTypes = {
  lanes: PropTypes.array.isRequired,
  rowChecks: PropTypes.array,
};

// ----------------------------------------------------------------- JSON

/** Per-lane raw trial JSON with copy and `Open full trace ↗`. */
export function JsonTab({ lanes, onOpenTrace }) {
  return (
    <Box
      sx={{ display: "grid", gridTemplateColumns: laneTemplate(lanes.length) }}
    >
      {lanes.map((l, i) => {
        const json = JSON.stringify(l.trial ?? {}, null, 2);
        return (
          <Col key={l.id} lane={l} i={i}>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{ mb: 0.5 }}
            >
              <CopyButton value={json} tooltip="Copy this trial as JSON" />
              <Button
                size="small"
                disabled={!l.trial?.trace_id}
                onClick={() => onOpenTrace(l.id)}
                endIcon={<Iconify icon="mdi:open-in-new" width={14} />}
              >
                Open full trace
              </Button>
            </Stack>
            <CodeBlock text={json} language="json" maxHeight={520} />
          </Col>
        );
      })}
    </Box>
  );
}
JsonTab.propTypes = {
  lanes: PropTypes.array.isRequired,
  onOpenTrace: PropTypes.func,
};
