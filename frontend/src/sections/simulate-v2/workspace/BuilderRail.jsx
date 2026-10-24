import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/* The folded builder: a slim rail that says what it is, shows when the
   builder is busy or has replied, and opens it again on click. */
export default function BuilderRail({ running, unseen, onExpand }) {
  return (
    <Stack
      alignItems="center"
      spacing={1.25}
      onClick={onExpand}
      sx={{
        height: "100%",
        py: 1.5,
        cursor: "pointer",
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Tooltip
        arrow
        placement="right"
        title={
          running
            ? "Builder is working — expand"
            : unseen > 0
              ? `${unseen} new from the builder — expand`
              : "Expand builder"
        }
      >
        <IconButton size="small" sx={{ color: "text.tertiary" }}>
          <Iconify icon="solar:double-alt-arrow-right-linear" width={16} />
        </IconButton>
      </Tooltip>
      <Box sx={{ position: "relative" }}>
        <Box
          sx={{
            /* Neutral, matching the builder's own header. */
            width: 30,
            height: 30,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            bgcolor: "action.hover",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:chat-round-line-linear" width={16} />
        </Box>
        {(running || unseen > 0) && (
          <Box
            sx={{
              position: "absolute",
              top: -3,
              right: -3,
              width: 9,
              height: 9,
              borderRadius: "50%",
              bgcolor: running
                ? "status.warning.solid"
                : "status.success.solid",
              border: "2px solid",
              borderColor: "background.default",
            }}
          />
        )}
      </Box>
    </Stack>
  );
}
BuilderRail.propTypes = {
  running: PropTypes.bool,
  unseen: PropTypes.number,
  onExpand: PropTypes.func,
};
