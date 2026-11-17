import PropTypes from "prop-types";
import { ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SEQUENCE_COPY as C } from "./sequence.constants";

// The problem's own words, without the step it names: the line starts with it.
const withoutStep = (text) => text.replace(/ (at )?step \d+$/, "");

const stepWords = (line) =>
  line.range ? C.stepsRange(line.range[0], line.range[1]) : C.step(line.n);

// One line per problem and per refusal; clicking a line opens its step.
function Line({ line, tone, text, onPick }) {
  return (
    <ButtonBase
      onClick={() => onPick(line.n)}
      data-testid="participants-problem"
      sx={{
        justifyContent: "flex-start",
        textAlign: "left",
        borderRadius: "4px",
        gap: 1,
      }}
    >
      <Iconify
        icon="solar:danger-circle-linear"
        width={14}
        sx={{ color: `${tone}.main`, flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s2", color: `${tone}.main`, fontWeight: 600 }}
      >
        {stepWords(line)}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.primary" }}>
        {text}
      </Typography>
    </ButtonBase>
  );
}

Line.propTypes = {
  line: PropTypes.object.isRequired,
  tone: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  onPick: PropTypes.func.isRequired,
};

export default function ParticipantsHeader({ summary, seq, onPick }) {
  const problems = seq?.problems || [];
  const refusals = seq?.refusals || [];
  if (!summary && !problems.length && !refusals.length) return null;
  return (
    <Stack spacing={0.5}>
      {summary ? (
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {summary}
        </Typography>
      ) : null}
      {problems.map((line) => (
        <Line
          key={`p-${line.n}`}
          line={line}
          tone="error"
          text={withoutStep(line.text)}
          onPick={onPick}
        />
      ))}
      {refusals.map((line) => (
        <Line
          key={`r-${line.group}`}
          line={line}
          tone="warning"
          text={C.refusedLine(line.tool, line.reason, line.times)}
          onPick={onPick}
        />
      ))}
    </Stack>
  );
}

ParticipantsHeader.propTypes = {
  summary: PropTypes.string,
  seq: PropTypes.object,
  onPick: PropTypes.func.isRequired,
};
