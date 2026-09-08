import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";

import { LIST_CURSOR_CONTINUATION_NOTICE } from "./listCursorPagination";

export default function ListCursorContinuationNotice({ pending, onContinue }) {
  if (!pending) return null;

  return (
    <Box
      role="status"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 1,
        px: 1.5,
        py: 0.75,
        color: "text.secondary",
        bgcolor: "action.hover",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography variant="caption">
        {LIST_CURSOR_CONTINUATION_NOTICE}
      </Typography>
      <Button size="small" variant="outlined" onClick={onContinue}>
        Continue search
      </Button>
    </Box>
  );
}

ListCursorContinuationNotice.propTypes = {
  pending: PropTypes.bool,
  onContinue: PropTypes.func.isRequired,
};
