import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CALL_WORKERS_COPY } from "../callTraces.constants";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import { KIND, json, smallBtn, took } from "./sequenceLayout";
import { CopyLink, Label, Pill, Quote } from "./SequenceParts";

const Missing = () => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {C.notInRecord}
  </Typography>
);

// The picked step: what was asked and what came back, in full, with links out.
export default function StepDetail({
  seq,
  step,
  onOpenTrace,
  onShowInTranscript,
  linkOf,
}) {
  const tool = step.kind === "tool";
  const to = tool ? step.tool : seq.nameOf(step.to);
  const a = step.answer;
  const waited = a?.kind === "none" && step.dur != null;
  const meta = [
    step.dur != null &&
      (waited ? C.waited(took(step.dur)) : C.took(took(step.dur))),
    step.turns > 1 && C.turns(step.turns),
    step.kind === "handoff" && step.tools > 0 && C.toolCalls(step.tools),
  ].filter(Boolean);
  const link = linkOf ? linkOf(step) : null;
  const endState =
    step.endState && step.endState !== "no_return"
      ? CALL_WORKERS_COPY.cutShort?.[step.endState]
      : null;
  const toCaller =
    step.from === seq.lanes[1]?.id && step.to === seq.lanes[0]?.id;

  const asked = tool ? (
    <>
      <Label>{C.input}</Label>
      <Box>
        {step.input != null ? (
          <Quote code>{json(step.input)}</Quote>
        ) : (
          <Missing />
        )}
      </Box>
    </>
  ) : (
    <>
      <Label>{toCaller ? C.said : C.asked}</Label>
      <Box>{step.full ? <Quote>{step.full}</Quote> : <Missing />}</Box>
    </>
  );
  const answered = tool ? (
    <>
      <Label>{C.result}</Label>
      <Box>
        {step.output != null ? (
          <Quote code bad={step.bad}>
            {json(step.output)}
          </Quote>
        ) : (
          <Typography
            sx={{
              typography: "s2",
              color: step.bad ? "error.main" : "text.tertiary",
            }}
          >
            {step.result}
          </Typography>
        )}
      </Box>
    </>
  ) : a ? (
    <>
      <Label>{C.answered}</Label>
      <Box>
        <Quote bad={a.bad}>{a.full || a.label}</Quote>
      </Box>
    </>
  ) : null;

  return (
    <Stack spacing={1.25} data-testid="step-detail">
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
      >
        <Pill n={step.n} bad={step.bad} refused={step.refused} />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`${seq.nameOf(step.from)} → ${to}`}
        </Typography>
        <Box
          component="span"
          sx={{
            typography: "s3",
            px: 0.75,
            borderRadius: "4px",
            bgcolor: "background.neutral",
            color: "text.secondary",
          }}
        >
          {KIND[step.kind]}
        </Box>
        <Box sx={{ flex: 1 }} />
        {onShowInTranscript && step.turn != null && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onShowInTranscript({ turn: step.turn, at: step.at })}
            startIcon={<Iconify icon="solar:play-circle-linear" width={14} />}
            sx={smallBtn}
          >
            {C.showInTranscript}
          </Button>
        )}
        {onOpenTrace && step.traceId && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onOpenTrace(step.traceId, step.spanId)}
            startIcon={
              <Iconify icon="solar:square-top-down-linear" width={14} />
            }
            sx={smallBtn}
          >
            {C.openInTrace}
          </Button>
        )}
        {link && <CopyLink url={link} />}
      </Stack>
      {meta.length > 0 && (
        <Typography
          sx={{
            typography: "s3",
            color: waited ? "error.main" : "text.tertiary",
            fontVariantNumeric: "tabular-nums",
            mt: "-4px !important",
          }}
        >
          {meta.join(" · ")}
        </Typography>
      )}
      {step.refused && (
        <Typography
          sx={{ typography: "s3", color: "warning.main", fontWeight: 600 }}
        >
          {C.refusedByWorld(step.reason)}
        </Typography>
      )}
      {endState && (
        <Typography
          data-testid="step-end-state"
          sx={{ typography: "s3", color: "warning.main", fontWeight: 600 }}
        >
          {C.cutShort(endState)}
        </Typography>
      )}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "88px minmax(0, 1fr)",
          rowGap: 1.25,
          columnGap: 1.5,
        }}
      >
        {asked}
        {answered}
      </Box>
    </Stack>
  );
}

StepDetail.propTypes = {
  seq: PropTypes.object.isRequired,
  step: PropTypes.object.isRequired,
  onOpenTrace: PropTypes.func,
  onShowInTranscript: PropTypes.func,
  linkOf: PropTypes.func,
};
