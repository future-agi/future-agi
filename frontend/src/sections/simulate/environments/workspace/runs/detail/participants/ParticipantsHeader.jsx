import PropTypes from "prop-types";
import { ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SEQUENCE_COPY as C } from "./sequence.constants";

// The problem's own words, without the step it names: the line starts with it.
const withoutStep = (text) => text.replace(/\s+(at |to )?step \d+$/i, "");

const stepWords = (line) =>
  line.range ? C.stepsRange(line.range[0], line.range[1]) : C.step(line.n);

// One line per problem and per refusal; clicking a line opens its step.
function Line({ line, tone, icon, text, onPick }) {
  return (
    <ButtonBase
      onClick={() => onPick(line.n)}
      data-testid="participants-problem"
      sx={{
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 0.75,
        color: `${tone}.main`,
        "&:hover .issue-text": { textDecoration: "underline" },
      }}
    >
      <Iconify icon={icon} width={14} sx={{ flexShrink: 0 }} />
      <Typography
        component="span"
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "inherit",
          whiteSpace: "nowrap",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {stepWords(line)}
      </Typography>
      <Typography
        component="span"
        className="issue-text"
        sx={{
          typography: "s3",
          color: "text.primary",
          textUnderlineOffset: "2px",
        }}
      >
        {text}
      </Typography>
    </ButtonBase>
  );
}

Line.propTypes = {
  line: PropTypes.object.isRequired,
  tone: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired,
  onPick: PropTypes.func.isRequired,
};

// The call in one line, then what the record is and what it covers.
export default function ParticipantsHeader({
  summary,
  notes = [],
  seq,
  onPick,
}) {
  const problems = seq?.problems || [];
  const refusals = seq?.refusals || [];
  if (!summary && !notes.length && !problems.length && !refusals.length)
    return null;
  return (
    <Stack spacing={0.5}>
      {summary ? (
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {summary}
        </Typography>
      ) : null}
      {/* What the record is and what it covers, said in plain text, never only on hover. */}
      {notes.map((note) => (
        <Typography
          key={note}
          data-testid="participants-note"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {note}
        </Typography>
      ))}
      {problems.map((line) => (
        <Line
          key={`p-${line.n}`}
          line={line}
          tone="error"
          icon="solar:danger-circle-linear"
          text={withoutStep(line.text)}
          onPick={onPick}
        />
      ))}
      {refusals.map((line) => (
        <Line
          key={`r-${line.group}`}
          line={line}
          tone="warning"
          icon="solar:forbidden-circle-linear"
          text={C.refusedLine(line.tool, line.reason, line.times)}
          onPick={onPick}
        />
      ))}
    </Stack>
  );
}

ParticipantsHeader.propTypes = {
  summary: PropTypes.string,
  notes: PropTypes.arrayOf(PropTypes.string),
  seq: PropTypes.object,
  onPick: PropTypes.func.isRequired,
};
