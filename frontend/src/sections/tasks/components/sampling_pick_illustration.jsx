import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { pickNames, rowNames } from "./simpleSamplingPolicy";

// Schematic only: these boxes explain containment, never pretend to be data or
// a preview. All essential information is in the visible text equivalent.
export default function SamplingPickIllustration({ unit, rowType }) {
  const rowUnit = { spans: "span", traces: "trace", sessions: "session" }[
    rowType
  ];
  const chain = [
    ...(["user", "attribute"].includes(unit) ? [unit] : []),
    "session",
    ...(rowType !== "sessions" ? ["trace"] : []),
    ...(rowType === "spans" ? ["span"] : []),
  ];
  const pickedIndex = chain.indexOf(unit);
  const rows = rowNames(rowType);
  const text =
    unit === rowUnit
      ? `Pick individual ${rows}. Only the picked ${rows} are evaluated.`
      : `Pick ${pickNames(unit)}. Every matching ${rows.replace(/s$/, "")} in each picked group is evaluated.`;
  const names = {
    span: "Spans",
    trace: "Traces",
    session: "Conversation",
    user: "End user",
    attribute: "Custom ID",
  };
  const nest = (index) => {
    const level = chain[index];
    const picked = index === pickedIndex;
    return (
      <Box
        key={level}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.75,
          p: 1,
          minWidth: 0,
          bgcolor: picked ? "action.selected" : "background.paper",
        }}
      >
        <Typography
          variant="caption"
          component="div"
          sx={{
            fontWeight: picked ? 600 : 400,
            mb: index < chain.length - 1 ? 0.75 : 0,
            overflowWrap: "anywhere",
          }}
        >
          {names[level]}
          {picked ? " · picked" : ""}
          {level === rowUnit ? " · evaluated" : ""}
        </Typography>
        {index + 1 < chain.length && nest(index + 1)}
      </Box>
    );
  };
  return (
    <Box role="figure" aria-label={text} sx={{ minWidth: 0 }}>
      <Box aria-hidden="true" sx={{ maxWidth: 380, mb: 1 }}>
        {nest(0)}
      </Box>
      <Typography variant="body2">{text}</Typography>
      {unit === "user" && (
        <Typography variant="caption" color="text.secondary">
          Users are the end users of your application.
        </Typography>
      )}
    </Box>
  );
}
SamplingPickIllustration.propTypes = {
  unit: PropTypes.string.isRequired,
  rowType: PropTypes.string.isRequired,
};
