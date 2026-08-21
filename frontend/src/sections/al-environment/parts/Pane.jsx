import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

/**
 * One section of a tab. The meta line on the right is not decoration — it carries the count
 * and the one sentence explaining what the reader is looking at.
 */
const Pane = ({ title, meta, children }) => (
  <Box
    sx={{
      bgcolor: "background.paper",
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1.5,
      px: 2,
      py: 1.75,
      mb: 2,
    }}
  >
    {(title || meta) && (
      <Stack
        direction="row"
        alignItems="baseline"
        justifyContent="space-between"
        spacing={2}
        sx={{ mb: children ? 1.25 : 0 }}
      >
        {title && (
          <Typography variant="subtitle2" sx={{ color: "text.primary" }}>
            {title}
          </Typography>
        )}
        {meta && (
          <Typography
            sx={{ fontFamily: ALK_MONO, fontSize: 11.5, color: "text.secondary", textAlign: "right" }}
          >
            {meta}
          </Typography>
        )}
      </Stack>
    )}
    {children}
  </Box>
);

Pane.propTypes = { title: PropTypes.string, meta: PropTypes.node, children: PropTypes.node };

export default Pane;
