import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

const StatusReadout = ({ spentUsd, busy }) => (
  <Stack direction="row" alignItems="center" spacing={1}>
    {busy && (
      <Box
        aria-label="working"
        sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: "accent.tool" }}
      />
    )}
    <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
      ${Number(spentUsd || 0).toFixed(4)}
    </Typography>
  </Stack>
);

StatusReadout.propTypes = {
  spentUsd: PropTypes.number,
  busy: PropTypes.bool,
};

export default StatusReadout;
