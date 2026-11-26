import PropTypes from "prop-types";
import { Box, Fade, Stack, Typography } from "@mui/material";
import CircularProgress from "@mui/material/CircularProgress";
import useMediaQuery from "@mui/material/useMediaQuery";

import { CONSOLE_COPY } from "../build.constants";

/** Over the list's top edge while the chat looks back for a message. */
export default function SearchStatus({ looking }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  return (
    // A zero-height anchor, so it floats over the list rather than moving it.
    <Box role="status" sx={{ position: "relative", height: 0, zIndex: 1 }}>
      <Fade in={looking} timeout={reduceMotion ? 0 : undefined} unmountOnExit>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="center"
          spacing={1}
          sx={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: 32,
            bgcolor: "background.paper",
            borderBottom: "1px solid",
            borderColor: "divider",
            color: "text.secondary",
            pointerEvents: "none",
          }}
        >
          <CircularProgress size={12} color="inherit" />
          <Typography sx={{ typography: "s1" }}>
            {CONSOLE_COPY.search.looking}
          </Typography>
        </Stack>
      </Fade>
    </Box>
  );
}
SearchStatus.propTypes = { looking: PropTypes.bool };
