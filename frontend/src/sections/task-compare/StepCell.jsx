import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Term from "src/sections/common/ux/Term";
import CopyButton from "src/sections/common/ux/CopyButton";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import CodeBlock from "src/sections/common/Values/CodeBlock";
import { InlineDetail } from "src/sections/common/ux/oneview";
import { fmtCost, fmtMs, fmtTokens } from "src/sections/common/format/fmt";
import { exactCall } from "./stepModel";

export const KIND_ICON = {
  customer: "mdi:account-voice",
  reply: "mdi:message-reply-text-outline",
  think: "mdi:brain",
  tool: "mdi:wrench-outline",
  retrieval: "mdi:book-search-outline",
  file_edit: "mdi:file-edit-outline",
  command: "mdi:console",
  test: "mdi:test-tube",
  subagent: "mdi:robot-outline",
  end: "mdi:flag-checkered",
};
export const KIND_WORD = {
  customer: "Customer turn",
  reply: "Reply",
  think: "Model step",
  tool: "Tool call",
  retrieval: "Retrieval",
  file_edit: "File edit",
  command: "Command",
  test: "Test run",
  subagent: "Subagent",
  end: "End state",
};

const parse = (v) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};
const show = (v) =>
  typeof v === "string"
    ? `"${v.length > 40 ? `${v.slice(0, 39)}…` : v}"`
    : JSON.stringify(v);

/** `order_id: "8830"` (first two keys) for objects; text for strings. */
export function argsSummary(input) {
  const v = parse(input);
  if (v == null) return "";
  if (Array.isArray(v))
    return `${v.length} ${v.length === 1 ? "message" : "messages"}`;
  if (typeof v === "object")
    return Object.entries(v)
      .filter(([, x]) => x != null && x !== "")
      .slice(0, 2)
      .map(([k, x]) => `${k}: ${show(x)}`)
      .join(", ");
  return String(v);
}

/** One-line result: the error, `id: "A-8830", status: "delivered"`, `3 items`, or text. */
export function resultSummary(step) {
  if (step?.error) return String(step.error);
  const v = parse(step?.output);
  if (v == null) return "";
  if (step.kind === "end")
    return (
      (v.changes ?? []).map((c) => c.text ?? c.path).join(" · ") || "No changes"
    );
  if (Array.isArray(v))
    return `${v.length} ${v.length === 1 ? "item" : "items"}${v[0]?.title ? `: ${v.map((x) => x.title).join(", ")}` : ""}`;
  if (typeof v === "object") return argsSummary(v);
  return String(v);
}

const isText = (v) => typeof parse(v) === "string";
const diffKind = (a, b) => (isText(a) && isText(b) ? "words" : "json");
const JSON_LINES = 20;

/** JSON / text: 20 lines, then `Show all (n lines)`. */
function JsonBlock({ value }) {
  const [all, setAll] = useState(false);
  const v = parse(value);
  if (typeof v === "string")
    return (
      <Typography
        typography="s2"
        sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {v}
      </Typography>
    );
  const text = JSON.stringify(v, null, 2) ?? "";
  const lines = text.split("\n");
  const long = lines.length > JSON_LINES;
  return (
    <Box>
      <CodeBlock
        text={long && !all ? lines.slice(0, JSON_LINES).join("\n") : text}
        language="json"
        maxHeight={all ? 480 : 0}
      />
      {long ? (
        <Button
          size="small"
          onClick={(e) => (e.stopPropagation(), setAll((x) => !x))}
          sx={{ typography: "s3", px: 0.5, minWidth: 0 }}
        >
          {all ? "Show less" : `Show all (${lines.length} lines)`}
        </Button>
      ) : null}
    </Box>
  );
}
JsonBlock.propTypes = { value: PropTypes.any };

function ValueBlock({ label, value, baseValue, diff }) {
  if (value == null && baseValue == null) return null;
  return (
    <Box sx={{ mt: 0.75 }}>
      <Typography typography="s3" color="text.secondary" sx={{ mb: 0.25 }}>
        {label}
        {diff ? " · vs baseline" : ""}
      </Typography>
      {diff ? (
        <TextDiff
          from={baseValue}
          to={value}
          kind={diffKind(baseValue, value)}
        />
      ) : (
        <JsonBlock value={value} />
      )}
    </Box>
  );
}
ValueBlock.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  baseValue: PropTypes.any,
  diff: PropTypes.bool,
};

const GLYPH = {
  ok: { icon: "mdi:check", color: "success.main", label: "Went fine" },
  warn: {
    icon: "mdi:alert-outline",
    color: "warning.main",
    label: "Went badly",
  },
  fail: { icon: "mdi:close", color: "error.main", label: "Failed" },
};
export const errTint = (th) =>
  th.palette.mode === "dark"
    ? "rgba(248,113,113,0.16)"
    : "rgba(239,68,68,0.07)";

/** `A-8830` never breaks at its hyphen. */
const ID_RE = /([A-Z]{1,4}-\d{2,})/;
function keepIds(t) {
  if (t == null) return t;
  return String(t)
    .split(ID_RE)
    .map((part, i) =>
      i % 2 ? (
        <Box component="span" key={i} sx={{ whiteSpace: "nowrap" }}>
          {part}
        </Box>
      ) : (
        part
      ),
    );
}

/** The closed line: `2  Looked up order 8830 → not found (404)   348 ms ✗`. */
function StepSummary({ line, open, wrap }) {
  const { step } = line;
  const bad = line.tone === "bad";
  const g = GLYPH[line.status] ?? GLYPH.ok;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={0.75}
      sx={{ minWidth: 0, width: "100%" }}
    >
      <Typography
        component="span"
        typography="s3"
        title={`${KIND_WORD[step.kind] ?? step.kind} · step ${step.n}${step.mappedFrom ? ` · mapped from ${step.mappedFrom}` : ""}`}
        sx={{
          width: 18,
          flexShrink: 0,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          fontWeight: 600,
          lineHeight: "20px",
          color: line.firstWrong ? "error.main" : "text.disabled",
        }}
      >
        {step.n}
      </Typography>
      <Typography
        component="span"
        typography="s2"
        title={wrap ? undefined : line.text}
        sx={{
          minWidth: 0,
          flex: 1,
          lineHeight: "20px",
          color: "text.primary",
          wordBreak: "break-word",
          ...(wrap
            ? {
                display: "-webkit-box",
                WebkitLineClamp: open ? 6 : 3,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }
            : {
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }),
        }}
      >
        {keepIds(line.action)}
        {line.result ? (
          <>
            <Box component="span" sx={{ color: "text.disabled", mx: 0.5 }}>
              →
            </Box>
            <Box
              component="span"
              sx={{
                color: bad ? "error.main" : "text.primary",
                fontWeight: bad ? 600 : 500,
              }}
            >
              {keepIds(line.result)}
            </Box>
          </>
        ) : null}
      </Typography>
      <Typography
        component="span"
        typography="s3"
        sx={{
          flexShrink: 0,
          color: "text.secondary",
          fontVariantNumeric: "tabular-nums",
          lineHeight: "20px",
          whiteSpace: "nowrap",
        }}
      >
        {Number.isFinite(step.ms) && step.kind !== "end" ? fmtMs(step.ms) : ""}
      </Typography>
      <Iconify
        icon={g.icon}
        width={14}
        aria-label={line.final ? `${line.final}: the run ended here` : g.label}
        sx={{
          flexShrink: 0,
          mt: "3px",
          color: g.color,
          opacity: line.status === "ok" ? 0.55 : 1,
        }}
      />
    </Stack>
  );
}
StepSummary.propTypes = {
  line: PropTypes.object.isRequired,
  open: PropTypes.bool,
  wrap: PropTypes.bool,
};

/** The open body: the exact call, arguments and result, time, tokens, cost. */
function ExactStep({
  line,
  base,
  isBase,
  maxMs,
  color,
  prompt,
  showPrompts,
  model,
  onOpenTrace,
  onOpenSpan,
}) {
  const { step } = line;
  const [promptOpen, setPromptOpen] = useState(false);
  const diffIn = !isBase && base && base.kind === step.kind;
  const call = exactCall(step);
  const facts = [
    Number.isFinite(step.tokens) && step.tokens ? fmtTokens(step.tokens) : null,
    Number.isFinite(step.cost) && step.cost ? fmtCost(step.cost) : null,
    Number.isFinite(step.ms) ? fmtMs(step.ms) : null,
  ].filter(Boolean);
  const isModel = step.kind === "think" || step.kind === "reply";
  return (
    <Box onClick={(e) => e.stopPropagation()} sx={{ cursor: "text", pb: 0.5 }}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography
          typography="s3"
          color="text.secondary"
          fontWeight={600}
          sx={{ flex: 1 }}
        >
          {/* IT-R5 G4 S4: the term says what it is. */}
          <Box
            component="span"
            title="Exact step: the call this step made as recorded in the trace (tool name, arguments and result, or the model's prompt and reply)"
            sx={{ textDecoration: "underline dotted", textUnderlineOffset: 3 }}
          >
            Exact step
          </Box>
        </Typography>
        <CopyButton
          value={() =>
            JSON.stringify(
              {
                step: step.n,
                name: step.name,
                input: step.input,
                output: step.output,
                error: step.error ?? undefined,
              },
              null,
              2,
            )
          }
          tooltip="Copy this step as JSON"
          size="small"
          iconSize={14}
        />
        {onOpenSpan ? (
          <Button
            size="small"
            onClick={onOpenSpan}
            sx={{ typography: "s3", px: 0.5, minWidth: 0 }}
            data-testid="open-span"
          >
            Open span
          </Button>
        ) : null}
        {onOpenTrace ? (
          <Button
            size="small"
            onClick={onOpenTrace}
            sx={{ typography: "s3", px: 0.5, minWidth: 0 }}
          >
            Open in trace ↗
          </Button>
        ) : null}
      </Stack>
      <Typography
        typography="s3"
        sx={{
          fontFamily: "monospace",
          wordBreak: "break-word",
          color: step.error ? "error.main" : "text.primary",
        }}
      >
        {call}
      </Typography>
      {step.input != null && step.kind !== "customer" ? (
        <ValueBlock
          label="Arguments"
          value={step.input}
          baseValue={base?.input}
          diff={Boolean(diffIn && base?.input != null)}
        />
      ) : null}
      <ValueBlock
        label={step.error ? "Result (error)" : "Result"}
        value={step.error && step.output == null ? step.error : step.output}
        baseValue={base?.output}
        diff={Boolean(diffIn && base?.output != null && !step.error)}
      />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 0.75 }}>
        <CustomTooltip
          describeChild
          show
          size="small"
          title={`${fmtMs(step.ms ?? 0)} · bars share one scale across lanes`}
        >
          <Box
            sx={{
              flex: 1,
              maxWidth: 160,
              height: 3,
              borderRadius: 2,
              bgcolor: "action.hover",
              overflow: "hidden",
            }}
          >
            <Box
              sx={{
                height: 3,
                width: `${Math.max(2, Math.min(100, ((step.ms ?? 0) / Math.max(1, maxMs)) * 100))}%`,
                bgcolor: color ?? "primary.main",
              }}
            />
          </Box>
        </CustomTooltip>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
        >
          {facts.join(" · ")}
        </Typography>
      </Stack>
      {isModel && model ? (
        <Typography typography="s3" color="text.secondary" sx={{ mt: 0.25 }}>
          Model {model}
        </Typography>
      ) : null}
      {prompt ? (
        showPrompts || promptOpen ? (
          <ValueBlock label="Prompt" value={prompt} />
        ) : (
          <Button
            size="small"
            onClick={() => setPromptOpen(true)}
            startIcon={<Iconify icon="mdi:text-box-outline" width={14} />}
            sx={{ typography: "s3", px: 0.5, mt: 0.25, minWidth: 0 }}
          >
            Prompt
          </Button>
        )
      ) : null}
    </Box>
  );
}
ExactStep.propTypes = {
  line: PropTypes.object.isRequired,
  base: PropTypes.object,
  isBase: PropTypes.bool,
  maxMs: PropTypes.number,
  color: PropTypes.string,
  prompt: PropTypes.any,
  showPrompts: PropTypes.bool,
  model: PropTypes.string,
  onOpenTrace: PropTypes.func,
  onOpenSpan: PropTypes.func,
};

/**
 * One step, one view (ONE-VIEW-SPEC §7.2): closed = one plain line (step
 * number · what happened · latency · ✓ / ⚠ / ✗); click, Enter, Space or →
 * opens the exact call in place (← or Esc closes); Shift+click or
 * Shift+Enter opens the same row in every lane. The first wrong step keeps
 * its red rule and the note that says what it should have been.
 */
export function OneStep({
  line,
  open,
  onToggle,
  onAcross,
  wrap,
  note,
  checkName = (k) => k,
  onFixStep,
  ...detail
}) {
  const fw = line.firstWrong;
  return (
    <Box
      data-step-n={line.n}
      data-first-wrong={fw ? "" : undefined}
      onKeyDown={(e) => {
        if (e.target?.getAttribute?.("aria-expanded") == null) return;
        if (e.key === "Escape" && open) {
          // An open step closes first; Esc goes back only with nothing open.
          e.preventDefault();
          e.stopPropagation();
          onToggle();
        } else if (e.key === "Enter" && e.shiftKey) {
          e.preventDefault();
          onAcross?.();
        } else if (e.key === "ArrowRight" && !open) {
          e.preventDefault();
          onToggle();
        } else if (e.key === "ArrowLeft" && open) {
          e.preventDefault();
          onToggle();
        }
      }}
      sx={{
        position: "relative",
        ...(fw
          ? {
              ml: "-10px",
              pl: "7px",
              borderLeft: "3px solid",
              borderColor: "error.main",
              bgcolor: errTint,
            }
          : null),
      }}
    >
      <InlineDetail
        open={open}
        chevron={false}
        label={`step ${line.n} · ${line.step.name}`}
        onToggle={(e) => (e?.shiftKey && onAcross ? onAcross() : onToggle())}
        summary={<StepSummary line={line} open={open} wrap={wrap} />}
        sx={{ "& > .MuiButtonBase-root": { px: 0.25, py: 0.125 } }}
      >
        <ExactStep line={line} {...detail} />
      </InlineDetail>
      {fw && note ? (
        <Typography
          typography="s2"
          sx={{ color: "error.main", fontWeight: 600, pl: "26px", pb: 0.25 }}
        >
          ✗ <Term k="firstWrongStep">First wrong step</Term>: {keepIds(note)}
          {onFixStep ? (
            <Button
              size="small"
              color="error"
              onClick={(e) => (e.stopPropagation(), onFixStep())}
              startIcon={<Iconify icon="mdi:wrench-cog-outline" width={14} />}
              sx={{ typography: "s3", ml: 1, px: 0.75, py: 0, minWidth: 0 }}
              data-testid="fix-this-step"
            >
              Fix this step
            </Button>
          ) : null}
        </Typography>
      ) : null}
      {!fw && line.failed.length ? (
        <Typography
          typography="s2"
          sx={{ color: "error.main", pl: "26px", pb: 0.25 }}
        >
          ✗ {line.failed.map(checkName).join(", ")} failed here
        </Typography>
      ) : null}
    </Box>
  );
}
OneStep.propTypes = {
  line: PropTypes.object.isRequired,
  open: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  onAcross: PropTypes.func,
  wrap: PropTypes.bool,
  note: PropTypes.string,
  checkName: PropTypes.func,
  onFixStep: PropTypes.func,
  onOpenSpan: PropTypes.func,
  base: PropTypes.object,
  isBase: PropTypes.bool,
  maxMs: PropTypes.number,
  color: PropTypes.string,
  prompt: PropTypes.any,
  showPrompts: PropTypes.bool,
  model: PropTypes.string,
  onOpenTrace: PropTypes.func,
};
