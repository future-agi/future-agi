// The Call lens transcript diff (R4 lane M): rows = turn pairs aligned by
// caller-turn index, one column per lane. The caller line shows what the
// agent heard against what the caller said (missing words struck in red,
// extra words underlined, turn WER in the tooltip); agent replies are
// word-diffed against the baseline lane (TextDiff); tool calls are chips;
// each answer carries its response gap and `⚡ Kept talking` where the
// agent went on over the caller. Click a turn (or `<` / `>` on the lens)
// to focus it; the lens seeks every lane there.
import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import { fmtMs, fmtPct, NUM_SX } from "src/sections/common/format/fmt";
import { heardTooltip, replyChange, toolChipText, wordDiff } from "./lensModel";

/** Replies that changed more than this read better as text than as a diff. */
const DIFF_MAX_CHANGE = 0.6;

export function HeardLine({ turn }) {
  const differs =
    turn.scripted_text != null && turn.scripted_text !== turn.text;
  const tip = heardTooltip(turn, { fmtPct });
  const body = differs ? (
    <>
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        sx={{ mr: 0.5 }}
      >
        Heard:
      </Typography>
      {wordDiff(turn.scripted_text, turn.text).map((w, i) => (
        <Typography
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          component="span"
          typography="s2"
          data-word={w.op}
          sx={{
            mr: 0.5,
            ...(w.op === "missing" && {
              color: "red.600",
              textDecoration: "line-through",
            }),
            ...(w.op === "extra" && {
              textDecoration: "underline",
              textDecorationStyle: "wavy",
              textDecorationColor: (t) => t.palette.warning.main,
            }),
          }}
        >
          {w.text}
        </Typography>
      ))}
    </>
  ) : (
    <Typography component="span" typography="s2">
      {turn.text}
    </Typography>
  );
  return (
    <CustomTooltip show={Boolean(tip)} title={tip} size="small" arrow>
      <Box sx={{ minWidth: 0, overflowWrap: "break-word" }}>{body}</Box>
    </CustomTooltip>
  );
}

HeardLine.propTypes = { turn: PropTypes.object.isRequired };

export function ToolChip({ tool }) {
  const error = tool.status === "error";
  return (
    <HoverCard
      title={tool.name}
      trigger={
        <Box
          component="span"
          data-tool-chip={tool.status}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            px: 0.75,
            py: 0.125,
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: error ? "red.300" : "divider",
            bgcolor: error ? "var(--score-red-light)" : "background.neutral",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            typography: "s3",
            color: error ? "red.700" : "text.primary",
            maxWidth: "100%",
          }}
        >
          <Iconify icon="mdi:tools" width={12} sx={{ flexShrink: 0 }} />
          <Box
            component="span"
            sx={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {toolChipText(tool)}
          </Box>
        </Box>
      }
      maxWidth={360}
    >
      <Box
        component="pre"
        sx={{
          m: 0,
          typography: "s3",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {`args ${JSON.stringify(tool.args ?? {}, null, 2)}\nresult ${JSON.stringify(tool.result ?? null, null, 2)}`}
      </Box>
    </HoverCard>
  );
}

ToolChip.propTypes = { tool: PropTypes.object.isRequired };

function AgentLine({ turn, baseText, isBaseline, textOnly, greeting }) {
  const kept = turn.interrupted_by === "caller";
  // Greetings differ between agents by design: shown as text, never diffed.
  const change =
    isBaseline || greeting || baseText == null
      ? 0
      : replyChange(baseText, turn.text);
  const asDiff =
    !isBaseline && baseText != null && change > 0 && change <= DIFF_MAX_CHANGE;
  return (
    <Stack gap={0.5} sx={{ minWidth: 0 }}>
      <Box sx={{ "& p": { typography: "s2" }, overflowWrap: "break-word" }}>
        {asDiff ? (
          <TextDiff from={baseText} to={turn.text} />
        ) : (
          <Typography typography="s2">
            {turn.text}
            {change > DIFF_MAX_CHANGE ? (
              <CustomTooltip
                show
                size="small"
                arrow
                title="Most of this reply differs from the baseline lane, so it is shown as text instead of a word diff."
              >
                <Typography
                  component="span"
                  typography="s3"
                  color="text.disabled"
                  data-different-reply=""
                  sx={{ ml: 0.75, whiteSpace: "nowrap" }}
                >
                  Different reply
                </Typography>
              </CustomTooltip>
            ) : null}
          </Typography>
        )}
      </Box>
      <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
        {turn.tool ? <ToolChip tool={turn.tool} /> : null}
        {Number.isFinite(turn.gap_ms) && !textOnly ? (
          <CustomTooltip
            show
            size="small"
            arrow
            title={
              turn.stage_ms
                ? `Response gap: the caller stopped, then the agent's first audio after ${fmtMs(turn.gap_ms)} (STT ${fmtMs(turn.stage_ms.stt)} · LLM ${fmtMs(turn.stage_ms.llm)} · TTS ${fmtMs(turn.stage_ms.tts)})`
                : `Response gap ${fmtMs(turn.gap_ms)}`
            }
          >
            <Typography
              typography="s3"
              color="text.secondary"
              data-gap={turn.gap_ms}
              sx={{
                ...NUM_SX,
                px: 0.5,
                borderRadius: 0.5,
                bgcolor: "background.neutral",
              }}
            >
              {fmtMs(turn.gap_ms)}
            </Typography>
          </CustomTooltip>
        ) : null}
        {kept && !textOnly ? (
          <CustomTooltip
            show
            size="small"
            arrow
            title="The caller started talking and the agent went on speaking over them. Under 1 s counts as stopping."
          >
            <Typography
              typography="s3"
              color="warning.dark"
              data-kept-talking=""
              sx={{ ...NUM_SX, fontWeight: 600 }}
            >
              {`⚡ Kept talking ${fmtMs(turn.agent_stop_ms)}`}
            </Typography>
          </CustomTooltip>
        ) : null}
      </Stack>
    </Stack>
  );
}

AgentLine.propTypes = {
  turn: PropTypes.object.isRequired,
  baseText: PropTypes.string,
  isBaseline: PropTypes.bool,
  textOnly: PropTypes.bool,
  greeting: PropTypes.bool,
};

const ROLE_TAG = { caller: "mdi:account-voice", agent: "mdi:headset" };

function TurnBlock({ role, focused, onClick, n, children, dataStep }) {
  return (
    <Box
      data-step={dataStep}
      data-focused={focused ? "true" : undefined}
      onClick={onClick}
      sx={{
        display: "grid",
        gridTemplateColumns: "18px 1fr",
        gap: 0.5,
        px: 1,
        py: 0.5,
        borderRadius: 0.5,
        cursor: "pointer",
        bgcolor: focused ? "action.selected" : "transparent",
        outline: focused ? "1px solid" : "none",
        outlineColor: "primary.main",
        "&:hover": { bgcolor: focused ? "action.selected" : "action.hover" },
      }}
    >
      <CustomTooltip
        show
        size="small"
        title={`${role === "caller" ? "Caller" : "Agent"} · turn ${n ?? "—"}`}
      >
        <Iconify
          icon={ROLE_TAG[role]}
          width={14}
          sx={{
            mt: 0.25,
            color: role === "caller" ? "text.disabled" : "text.secondary",
          }}
          aria-label={role === "caller" ? "Caller" : "Agent"}
        />
      </CustomTooltip>
      <Box sx={{ minWidth: 0 }}>{children}</Box>
    </Box>
  );
}

TurnBlock.propTypes = {
  role: PropTypes.string,
  focused: PropTypes.bool,
  onClick: PropTypes.func,
  n: PropTypes.number,
  children: PropTypes.node,
  dataStep: PropTypes.string,
};

/**
 * @param {{rows: object[], lanes: object[], calls: object[],
 *   focusStep: object|null, onFocusStep: (step) => void, steps: object[],
 *   callerName?: string}} props
 */
export default function TranscriptDiff({
  rows,
  lanes,
  calls,
  steps,
  focusStep,
  onFocusStep,
  callerName = "Caller",
}) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (!focusStep || !scrollRef.current) return;
    const el = scrollRef.current.querySelector(
      `[data-step-row="${focusStep.id}"]`,
    );
    // IT-R2 G4 P6: the focused (or playing) turn sits in the middle of the
    // transcript, the turns before and after it in view (this scroller
    // only; the page never moves).
    const box = scrollRef.current;
    if (!el || !box) return;
    const top =
      el.getBoundingClientRect().top -
      box.getBoundingClientRect().top +
      box.scrollTop;
    box.scrollTo?.({
      top: Math.max(0, top - (box.clientHeight - el.offsetHeight) / 2),
      behavior: "smooth",
    });
  }, [focusStep]);
  const cols = `44px repeat(${lanes.length}, minmax(0, 1fr))`;
  const stepFor = (k, slot) => steps.find((s) => s.k === k && s.slot === slot);
  return (
    <Box
      ref={scrollRef}
      data-transcript-diff=""
      sx={{ flex: 1, minHeight: 0, overflow: "auto", position: "relative" }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: cols,
          position: "sticky",
          top: 0,
          zIndex: 1,
          bgcolor: "background.paper",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          typography="s3"
          color="text.disabled"
          sx={{ px: 1, py: 0.5 }}
        >
          Turn
        </Typography>
        {lanes.map((l) => (
          <Stack
            key={l.runId}
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ px: 1, py: 0.5, minWidth: 0 }}
          >
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                bgcolor: l.color,
                flexShrink: 0,
              }}
            />
            <Typography typography="s3" color="text.secondary" noWrap>
              {l.label}
            </Typography>
          </Stack>
        ))}
      </Box>
      {rows.map((row) => {
        const maxAgents = Math.max(
          0,
          ...row.lanes.map((x) => (x ? x.agents.length : 0)),
        );
        const slots = [
          ...(row.lanes.some((x) => x?.caller) ? [0] : []),
          ...Array.from({ length: maxAgents }, (_, i) => i + 1),
        ];
        return slots.map((slot) => {
          const step = stepFor(row.k, slot);
          if (!step) return null;
          const focused = focusStep?.id === step.id;
          return (
            <Box
              key={step.id}
              data-step-row={step.id}
              sx={{
                display: "grid",
                gridTemplateColumns: cols,
                borderBottom:
                  slot === slots[slots.length - 1] ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography
                typography="s3"
                color={focused ? "primary.main" : "text.disabled"}
                sx={{
                  ...NUM_SX,
                  textAlign: "right",
                  px: 1,
                  pt: 0.75,
                  fontWeight: focused ? 600 : 400,
                }}
              >
                {step.n ?? "—"}
                {(() => {
                  // IT-R5 G4 P5: each turn's `mm:ss` (the first lane that
                  // has it); a click seeks every lane there.
                  const at = row.lanes
                    .map((x) =>
                      !x ? null : slot === 0 ? x.caller : x.agents[slot - 1],
                    )
                    .find((t) => Number.isFinite(t?.start_ms))?.start_ms;
                  if (!Number.isFinite(at)) return null;
                  const sec = Math.floor(at / 1000);
                  const mmss = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
                  return (
                    <Box
                      component="button"
                      type="button"
                      data-turn-time={mmss}
                      aria-label={`Play from ${mmss} (turn ${step.n})`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onFocusStep?.(step);
                      }}
                      sx={{
                        all: "unset",
                        display: "block",
                        cursor: "pointer",
                        fontSize: 10,
                        color: "text.disabled",
                        "&:hover": {
                          color: "primary.main",
                          textDecoration: "underline",
                        },
                        "&:focus-visible": { outline: "2px solid" },
                      }}
                    >
                      {mmss}
                    </Box>
                  );
                })()}
              </Typography>
              {row.lanes.map((lane, i) => {
                const call = calls[i];
                const textOnly = call?.depth === "text_only";
                const turn = !lane
                  ? null
                  : slot === 0
                    ? lane.caller
                    : lane.agents[slot - 1];
                if (!turn)
                  return (
                    <Box key={lanes[i].runId} sx={{ px: 1, py: 0.75 }}>
                      {!lane && slot === slots[0] ? (
                        <Typography typography="s3" color="text.disabled">
                          {call ? "Call already ended" : ""}
                        </Typography>
                      ) : null}
                    </Box>
                  );
                const base = row.lanes[0];
                const baseTurn = base
                  ? slot === 0
                    ? base.caller
                    : base.agents[slot - 1]
                  : null;
                return (
                  <TurnBlock
                    key={lanes[i].runId}
                    role={slot === 0 ? "caller" : "agent"}
                    n={turn.i}
                    focused={focused}
                    dataStep={step.id}
                    onClick={() => onFocusStep(step)}
                  >
                    {slot === 0 ? (
                      <Stack gap={0.25}>
                        {i === 0 || lanes.length === 1 ? (
                          <Typography typography="s3" color="text.disabled">
                            {callerName}
                          </Typography>
                        ) : null}
                        <HeardLine turn={turn} />
                      </Stack>
                    ) : (
                      <AgentLine
                        turn={turn}
                        isBaseline={i === 0}
                        greeting={row.k === 0}
                        baseText={baseTurn?.text ?? null}
                        textOnly={textOnly}
                      />
                    )}
                  </TurnBlock>
                );
              })}
            </Box>
          );
        });
      })}
    </Box>
  );
}

TranscriptDiff.propTypes = {
  rows: PropTypes.array.isRequired,
  lanes: PropTypes.array.isRequired,
  calls: PropTypes.array.isRequired,
  steps: PropTypes.array.isRequired,
  focusStep: PropTypes.object,
  onFocusStep: PropTypes.func.isRequired,
  callerName: PropTypes.string,
};
