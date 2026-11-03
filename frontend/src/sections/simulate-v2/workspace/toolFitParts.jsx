import PropTypes from "prop-types";
import { Box } from "@mui/material";

/* Small shared pieces for anything that talks about tools and fit. */

export const Mono = ({ children }) => (
  <Box
    component="span"
    sx={{
      fontFamily: "ui-monospace, Menlo, monospace",
      fontSize: "0.92em",
      fontWeight: 600,
      px: 0.5,
      py: "1px",
      borderRadius: "4px",
      bgcolor: "action.hover",
      color: "text.primary",
    }}
  >
    {children}
  </Box>
);
Mono.propTypes = { children: PropTypes.node };

/* "a, b and c" — each tool name set in mono. */
export const ToolList = ({ tools }) =>
  tools.map((t, i) => (
    <Box component="span" key={t.name}>
      {i > 0 && (i === tools.length - 1 ? " and " : ", ")}
      <Mono>{t.name}</Mono>
    </Box>
  ));
ToolList.propTypes = { tools: PropTypes.array.isRequired };

/*
  A tool the agent version calls with other arguments than the world answers
  (toolFit's `changed`). Says what changed and which scenarios it blocks on
  this version; runs already made on earlier versions keep their results.
*/
export const ChangedToolsNote = ({ changed, blocked, agent, sx }) => {
  if (!changed?.length) return null;
  const n = blocked?.length || 0;
  return (
    <Box
      component="div"
      sx={{ typography: "s2", color: "text.secondary", ...sx }}
    >
      {changed.map((t) => (
        <Box component="div" key={t.name} sx={{ mb: 0.5 }}>
          <Mono>{t.name}</Mono>
          {` now takes ${(t.params || []).join(", ")}`}
          {t.was?.length ? ` (was ${t.was.join(", ")})` : ""}.
        </Box>
      ))}
      {n
        ? `${n} ${n === 1 ? "scenario calls" : "scenarios call"} ${changed.length === 1 ? "it" : "them"} and ${n === 1 ? "is" : "are"} blocked on agent ${agent}: ${n === 1 ? "it comes" : "they come"} back not measured until the world is rebuilt for the new arguments. `
        : "No scenario calls it yet. "}
      Runs on earlier versions keep their results.
    </Box>
  );
};
ChangedToolsNote.propTypes = {
  changed: PropTypes.array,
  blocked: PropTypes.array,
  agent: PropTypes.string,
  sx: PropTypes.object,
};
