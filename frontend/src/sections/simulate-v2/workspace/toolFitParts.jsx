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
