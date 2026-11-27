import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../SideDrawer";
import LogViewer from "./LogViewer";

/** A sandbox's full log in a side drawer (960 wide). */
export default function LogsDrawer({
  open,
  onClose,
  log,
  title,
  subtitle,
  focusLineId,
}) {
  return (
    <SideDrawer open={open} onClose={onClose} width={960}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              {title || log?.title || "Logs"}
            </Typography>
            {subtitle && (
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {subtitle}
              </Typography>
            )}
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close logs">
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}>
          {open && <LogViewer log={log} focusLineId={focusLineId} />}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

LogsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  log: PropTypes.object,
  title: PropTypes.string,
  subtitle: PropTypes.string,
  focusLineId: PropTypes.string,
};
