import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CALL_WORKERS_COPY } from "../callTraces.constants";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import ChatMarkdown from "src/sections/simulate/environments/buildEnvironment/console/ChatMarkdown";
import { KIND, asMarkdown, readable, smallBtn, took } from "./sequenceLayout";
import { CopyLink, Label, Pill, Quote } from "./SequenceParts";

// Nothing to show: the record dropped it at its size limit, or never had it.
const Missing = ({ notKept = false }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {notKept ? C.notKept : C.notInRecord}
  </Typography>
);

Missing.propTypes = { notKept: PropTypes.bool };

const SCROLL_SX = { maxHeight: 320, overflow: "auto" };

// Data sits in the same box as text; the viewer's own fill would show as a second, darker box.
function Json({ data, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={{
        mt: 0.5,
        px: 1.25,
        py: 1,
        borderRadius: "6px",
        bgcolor: "background.neutral",
        ...SCROLL_SX,
      }}
    >
      <CustomJsonViewer
        object={data}
        sx={{ fontSize: "12px", lineHeight: "22px", bgcolor: "transparent" }}
      />
    </Box>
  );
}

Json.propTypes = { data: PropTypes.any.isRequired, testId: PropTypes.string };

// An object or array the text holds as JSON, else null.
const parsedJson = (text) => {
  if (typeof text !== "string" || !/^\s*[[{]/.test(text)) return null;
  try {
    const value = JSON.parse(text);
    return value !== null && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
};

// A message as the API formatted it: prose as markdown, data as a code block.
function Message({ text, shown, bad }) {
  const data = shown?.format === "code" ? parsedJson(shown.text) : null;
  if (data) return <Json data={data} testId="message-json" />;
  if (!shown) return <Quote bad={bad}>{readable(text)}</Quote>;
  return (
    <Box
      sx={{
        mt: 0.5,
        px: 1.25,
        py: 1,
        borderRadius: "6px",
        bgcolor: "background.neutral",
        wordBreak: "break-word",
        ...SCROLL_SX,
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          typography: "s2",
          fontWeight: 600,
          my: 0.25,
        },
        // Data in a message wraps instead of scrolling sideways.
        "& pre, & code": { whiteSpace: "pre-wrap", wordBreak: "break-word" },
        "& pre": { overflowX: "visible" },
      }}
    >
      {/* Worker text isn't ours: ChatMarkdown shows raw HTML as text and never loads an image. */}
      <ChatMarkdown text={asMarkdown(shown)} dim sx={{ typography: "s2" }} />
    </Box>
  );
}

Message.propTypes = {
  text: PropTypes.string,
  shown: PropTypes.shape({ format: PropTypes.string, text: PropTypes.string }),
  bad: PropTypes.bool,
};

// A tool's input or output as recorded: an object in the JSON viewer, text with its line breaks.
function Payload({ value, bad, testId }) {
  const data =
    value != null && typeof value === "object" ? value : parsedJson(value);
  if (data) return <Json data={data} testId={testId} />;
  return (
    <Box sx={SCROLL_SX}>
      <Quote code bad={bad}>
        <span data-testid={testId}>{readable(value)}</span>
      </Quote>
    </Box>
  );
}

Payload.propTypes = {
  value: PropTypes.any,
  bad: PropTypes.bool,
  testId: PropTypes.string,
};

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
  const waited = a?.kind === "none" && !a.cut && !a.stopped && step.dur != null;
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
          <Payload value={step.input} testId="tool-input" />
        ) : (
          <Missing notKept={step.inputOmitted} />
        )}
      </Box>
    </>
  ) : (
    <>
      <Label>{toCaller ? C.said : C.asked}</Label>
      <Box>
        {step.full ? (
          <Message text={step.full} shown={step.fullShown} />
        ) : (
          <Missing />
        )}
      </Box>
    </>
  );
  const answered = tool ? (
    <>
      <Label>{C.result}</Label>
      <Box>
        {step.output != null ? (
          <Payload value={step.output} bad={step.bad} testId="tool-result" />
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
        <Message text={a.full || a.label} shown={a.fullShown} bad={a.bad} />
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
