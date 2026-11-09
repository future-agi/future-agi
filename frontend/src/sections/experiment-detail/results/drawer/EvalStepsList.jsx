// W1-9 B3 (notify E39) · Eval steps: after the task steps, one step per
// eval or check that judged this try, from the same trace query the
// trajectory reads (useTraceTree: no second fetch). A row reads
// `Correctness · judge gpt-4o · Fail · "reason" · 1.2 s · $0.0004`;
// expanding it shows the verdict, the reason, the inputs it read, the step
// it judged, and `Open eval details` (the eval's own trace).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import useTraceTree from "src/sections/common/Trajectory/useTraceTree";
import {
  evalStepLine,
  evalStepsOf,
} from "src/sections/common/Trajectory/evalSteps";

const TONE = {
  fail: "accent.fail",
  error: "error.main",
  na: "info.main",
  skipped: "text.disabled",
  not_reached: "text.disabled",
  pass: "accent.pass",
};
const ICON = {
  fail: "mdi:close-circle-outline",
  error: "mdi:alert-outline",
  na: "mdi:minus-circle-outline",
  skipped: "mdi:debug-step-over",
  not_reached: "mdi:debug-step-over",
  pass: "mdi:check-circle-outline",
};

function Detail({ label, children }) {
  return (
    <Stack direction="row" gap={1} sx={{ minWidth: 0 }}>
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ width: 96, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Typography
        typography="s3"
        sx={{ minWidth: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {children}
      </Typography>
    </Stack>
  );
}
Detail.propTypes = { label: PropTypes.string, children: PropTypes.node };

function EvalStepRow({ step, onOpenTrace }) {
  const [open, setOpen] = useState(false);
  return (
    <Box
      data-testid="eval-step"
      data-kind={step.kind}
      sx={{ borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        sx={{ px: 1.25, py: 0.6, cursor: "pointer", minWidth: 0 }}
      >
        <Iconify
          icon={ICON[step.kind]}
          width={14}
          sx={{ color: TONE[step.kind], flexShrink: 0 }}
        />
        <Typography
          typography="s2"
          noWrap
          sx={{ minWidth: 0, flex: 1 }}
          data-testid="eval-step-line"
        >
          {evalStepLine(step)}
        </Typography>
        <Iconify
          icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack gap={0.5} sx={{ px: 1.25, pb: 1, pl: 4 }}>
          <Detail label="Verdict">
            {`${step.verdict}${step.score != null ? ` · ${step.score} / 100` : ""}`}
          </Detail>
          {step.reason ? <Detail label="Reason">{step.reason}</Detail> : null}
          {step.inputs ? (
            <Detail label="Inputs read">
              {Object.entries(step.inputs)
                .map(
                  ([k, v]) =>
                    `${k} ← ${typeof v === "string" ? v : JSON.stringify(v)}`,
                )
                .join("\n")}
            </Detail>
          ) : null}
          <Detail label="Judged step">{step.spanName}</Detail>
          {step.judgeModel ? (
            <Detail label="Judge">{step.judgeModel}</Detail>
          ) : null}
          {step.version ? (
            <Detail label="Version">{step.version}</Detail>
          ) : null}
          {/* review S8: only a verdict with a recorded eval trace opens
              one (an engine check's verdict has none: no button) */}
          {step.evalTraceId ? (
            <Box>
              <Button
                size="small"
                color="inherit"
                onClick={() => onOpenTrace(step.evalTraceId)}
                endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
                sx={{ typography: "s2", px: 0.75 }}
                data-testid="eval-step-open"
              >
                Open eval details
              </Button>
            </Box>
          ) : null}
        </Stack>
      </Collapse>
    </Box>
  );
}
EvalStepRow.propTypes = {
  step: PropTypes.object.isRequired,
  onOpenTrace: PropTypes.func.isRequired,
};

export default function EvalStepsList({ traceId, runId, runLabel }) {
  const { spans } = useTraceTree(traceId ?? null);
  const steps = useMemo(
    () => evalStepsOf(spans, { runId, runLabel }),
    [spans, runId, runLabel],
  );
  const [evalTrace, setEvalTrace] = useState(null);
  if (!traceId || !steps.length) return null;
  const failing = steps.filter((s) => s.kind === "fail").length;
  return (
    <Box
      data-testid="eval-steps"
      sx={{
        mx: 2,
        mb: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        gap={1}
        sx={{ px: 1.25, py: 0.6 }}
      >
        <Typography typography="s2" fontWeight="fontWeightSemiBold">
          Eval steps
        </Typography>
        <Typography typography="s3" color="text.secondary">
          {`${steps.length} ${steps.length === 1 ? "verdict" : "verdicts"} on this try${failing ? ` · ${failing} failing` : ""}`}
        </Typography>
      </Stack>
      {steps.map((s) => (
        <EvalStepRow key={s.key} step={s} onOpenTrace={setEvalTrace} />
      ))}
      {evalTrace ? (
        <TraceDetailDrawerV2
          traceId={evalTrace}
          open
          onClose={() => setEvalTrace(null)}
        />
      ) : null}
    </Box>
  );
}

EvalStepsList.propTypes = {
  traceId: PropTypes.string,
  runId: PropTypes.string,
  runLabel: PropTypes.string,
};
