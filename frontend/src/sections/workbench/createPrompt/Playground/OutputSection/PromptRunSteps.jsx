// IT-R1 G4 · what the model and its tools did, above a playground answer:
// `Model → lookup_order({"order_id":"A-8216"}) → result {…} → final answer`,
// or `The model answered without calling lookup_order` when tools were
// attached and none was called. Reads the run's additive metadata (`steps`
// = run events `{kind: model|tool_call|tool_result|final, text, name?,
// args?, result?}`, `tools` = attached tool names); renders nothing without
// them.
// IT-R2 G4 P1: the no-call line says why (`Tool choice is Auto — set
// Required to force a call, or say in the prompt when to call it.`) with
// `Set Required` (sets the choice and runs again) and `View request`
// (messages, tools and tool_choice as JSON); the step list sits above the
// answer.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { toolChoiceLabel } from "../ToolChoiceSelect";

const KIND = {
  model: { icon: "mdi:robot-outline", label: "Model" },
  tool_call: { icon: "mdi:wrench-outline", label: "Tool call" },
  tool_result: { icon: "mdi:keyboard-return", label: "Result" },
  final: { icon: "mdi:message-reply-text-outline", label: "Final answer" },
};

const orList = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}`;

/** The final answer of a run (the `final` event), or null. */
export function finalAnswerOf(steps) {
  const f = [...(steps ?? [])].reverse().find((e) => e?.kind === "final");
  return f?.text ?? null;
}

/** Why no tool was called, from the run's tool choice. Pure. */
export function noCallWhy(toolChoice) {
  const label = toolChoiceLabel(toolChoice);
  if (label === "Auto")
    return "Tool choice is Auto — set Required to force a call, or say in the prompt when to call it.";
  if (label === "None")
    return "Tool choice is None — the model may not call tools.";
  return `Tool choice is ${label}, but the model still answered directly.`;
}

/** `The model answered without calling lookup_order` or null. Pure. */
export function noCallLine(steps, tools) {
  const names = (tools ?? []).filter(Boolean);
  if (!names.length || !(steps ?? []).length) return null;
  if (steps.some((e) => e?.kind === "tool_call")) return null;
  return `The model answered without calling ${orList(names)}.`;
}

function Mono({ children }) {
  return (
    <Box
      component="span"
      sx={{
        fontFamily: "ui-monospace, SFMono-Regular, monospace",
        fontSize: 12,
        wordBreak: "break-word",
      }}
    >
      {children}
    </Box>
  );
}
Mono.propTypes = { children: PropTypes.node };

// IT-R4 G4 P3: a step's arguments / result as collapsible JSON.
function JsonToggle({ label, value, open, onToggle }) {
  return (
    <Stack gap={0.5} sx={{ minWidth: 0 }}>
      <Box
        component="button"
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        sx={{
          all: "unset",
          cursor: "pointer",
          typography: "s2",
          color: "text.primary",
          "&:hover": { textDecoration: "underline" },
          "&:focus-visible": { outline: "2px solid", outlineOffset: 1 },
        }}
      >
        {label}
        <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
          {open ? "Hide JSON" : "Show JSON"}
        </Box>
      </Box>
      {open ? (
        <Box
          component="pre"
          sx={{
            m: 0,
            p: 1,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
            maxHeight: 220,
            overflow: "auto",
            fontSize: 12,
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          {JSON.stringify(value ?? null, null, 2)}
        </Box>
      ) : null}
    </Stack>
  );
}
JsonToggle.propTypes = {
  label: PropTypes.node,
  value: PropTypes.any,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
};

const fmtStepMs = (ms) =>
  !Number.isFinite(ms) || ms <= 0
    ? null
    : ms < 1000
      ? `${Math.round(ms)} ms`
      : `${(ms / 1000).toFixed(1)} s`;

function StepRow({ step, last, ms }) {
  const [open, setOpen] = useState(false);
  const k = KIND[step.kind] ?? KIND.model;
  let body;
  if (step.kind === "tool_call")
    body = (
      <JsonToggle
        label={
          <Mono>{`${step.name}(${JSON.stringify(step.args ?? {})})`}</Mono>
        }
        value={step.args ?? {}}
        open={open}
        onToggle={() => setOpen((v) => !v)}
      />
    );
  else if (step.kind === "tool_result")
    body = (
      <Stack gap={0.5} sx={{ minWidth: 0 }}>
        <Box
          component="button"
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          sx={{
            all: "unset",
            cursor: "pointer",
            typography: "s2",
            color: "text.primary",
            "&:hover": { textDecoration: "underline" },
            "&:focus-visible": { outline: "2px solid", outlineOffset: 1 },
          }}
        >
          {step.text}
          <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
            {open ? "Hide JSON" : "Show JSON"}
          </Box>
        </Box>
        {open ? (
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              maxHeight: 220,
              overflow: "auto",
              fontSize: 12,
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
            }}
          >
            {JSON.stringify(step.result ?? null, null, 2)}
          </Box>
        ) : null}
      </Stack>
    );
  else
    body = (
      <Typography typography="s2" color="text.primary" sx={{ minWidth: 0 }}>
        {step.kind === "final" ? "Shown below" : step.text}
      </Typography>
    );
  return (
    <Stack direction="row" gap={1} sx={{ minWidth: 0 }}>
      <Stack alignItems="center" sx={{ flexShrink: 0, width: 20 }}>
        <Iconify
          icon={k.icon}
          width={16}
          sx={{ color: "text.secondary", mt: "2px" }}
        />
        {last ? null : (
          <Box sx={{ flex: 1, width: "1px", bgcolor: "divider", my: 0.25 }} />
        )}
      </Stack>
      <Stack sx={{ minWidth: 0, pb: last ? 0 : 1 }}>
        <Typography typography="s3" color="text.secondary">
          {k.label}
          {step.kind === "tool_result" ? " (mock)" : ""}
          {step.kind === "tool_call" && step.name ? ` · ${step.name}` : ""}
          {step.kind === "tool_result" && step.result?.error ? " · error" : ""}
          {fmtStepMs(ms) ? ` · ${fmtStepMs(ms)}` : ""}
          {Number.isFinite(step.tokens) ? ` · ${step.tokens} tokens` : ""}
        </Typography>
        {body}
      </Stack>
    </Stack>
  );
}
StepRow.propTypes = {
  step: PropTypes.object.isRequired,
  last: PropTypes.bool,
  ms: PropTypes.number,
};

/** The request a run sent (messages, tools, tool_choice), as JSON. */
function RequestDialog({ open, onClose, request }) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pr: 6 }}>
        Request sent to the model
        <IconButton
          aria-label="Close"
          onClick={onClose}
          sx={{ position: "absolute", right: 12, top: 12 }}
        >
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
          Messages (variables filled in), tools and tool_choice, as sent.
        </Typography>
        <Box
          component="pre"
          data-testid="prompt-run-request"
          sx={{
            m: 0,
            p: 1.5,
            borderRadius: 0.5,
            bgcolor: "background.neutral",
            maxHeight: "60vh",
            overflow: "auto",
            fontSize: 12,
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
          }}
        >
          {JSON.stringify(request ?? null, null, 2)}
        </Box>
      </DialogContent>
    </Dialog>
  );
}
RequestDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  request: PropTypes.object,
};

export default function PromptRunSteps({
  steps,
  tools,
  toolChoice,
  request,
  onSetRequired,
}) {
  const [showRequest, setShowRequest] = useState(false);
  const [setting, setSetting] = useState(false);
  const list = (steps ?? []).filter((e) => KIND[e?.kind]);
  // IT-R5 G4 P3: steps stream in; "answered without calling" only once
  // the model has answered (a final step).
  const none = list.some((e) => e?.kind === "final")
    ? noCallLine(list, tools)
    : null;
  if (!list.length) return null;
  const requestButton = request ? (
    <Button
      size="small"
      variant="text"
      onClick={() => setShowRequest(true)}
      sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
    >
      View request
    </Button>
  ) : null;
  const dialog = request ? (
    <RequestDialog
      open={showRequest}
      onClose={() => setShowRequest(false)}
      request={request}
    />
  ) : null;
  if (none)
    return (
      <Stack
        direction="row"
        flexWrap="wrap"
        columnGap={1}
        rowGap={0.75}
        alignItems="flex-start"
        data-testid="prompt-run-no-call"
        sx={{
          mb: 1.5,
          px: 1.25,
          py: 0.75,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "warning.light",
          bgcolor: "background.paper",
        }}
      >
        <Iconify
          icon="mdi:information-outline"
          width={16}
          sx={{ color: "warning.main", flexShrink: 0, mt: "3px" }}
        />
        <Typography
          typography="s2"
          color="text.primary"
          sx={{ flex: "1 1 220px", minWidth: 0 }}
        >
          {none}{" "}
          <Box component="span" sx={{ color: "text.secondary" }}>
            {noCallWhy(toolChoice)}
          </Box>
        </Typography>
        <Stack direction="row" gap={0.5} sx={{ flexShrink: 0, ml: "auto" }}>
          {onSetRequired && toolChoiceLabel(toolChoice) === "Auto" ? (
            <Button
              size="small"
              variant="outlined"
              disabled={setting}
              onClick={() => {
                setSetting(true);
                onSetRequired();
              }}
              sx={{ whiteSpace: "nowrap" }}
            >
              {setting ? "Running with Required…" : "Set Required"}
            </Button>
          ) : null}
          {requestButton}
        </Stack>
        {dialog}
      </Stack>
    );
  if (!list.some((e) => e.kind === "tool_call")) return null;
  return (
    <Stack
      gap={0.75}
      data-testid="prompt-run-steps"
      sx={{
        mb: 1.5,
        p: 1.25,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          What the model did · {list.length} steps
          {toolChoice ? (
            <Box
              component="span"
              sx={{ color: "text.secondary", fontWeight: 400 }}
            >
              {` · tool choice ${toolChoiceLabel(toolChoice)}`}
            </Box>
          ) : null}
        </Typography>
        {requestButton}
      </Stack>
      <Stack>
        {list.map((e, i) => (
          <StepRow
            key={i}
            step={e}
            last={i === list.length - 1}
            ms={
              Number.isFinite(e.at_ms)
                ? e.at_ms - (i ? list[i - 1].at_ms ?? 0 : 0)
                : undefined
            }
          />
        ))}
      </Stack>
      {dialog}
    </Stack>
  );
}

PromptRunSteps.propTypes = {
  steps: PropTypes.arrayOf(PropTypes.object),
  tools: PropTypes.arrayOf(PropTypes.string),
  toolChoice: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
  request: PropTypes.object,
  onSetRequired: PropTypes.func,
};
