import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import TemplateBuildPanel from "./TemplateBuildPanel";
import { QuietTag } from "./LandingBits";

/*
  A prebuilt world, opened from its card: what's in it, and the agent whose
  environment it starts — in a drawer, so the list stays where it was.
*/
export default function WorldDrawer({ world, onClose }) {
  return (
    <SideDrawer open={!!world} onClose={onClose} width={640}>
      {world && (
        <Box
          sx={{
            height: "100%",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 2.5,
              py: 1.75,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
                  {world.name}
                </Typography>
                <QuietTag>{world.typeLabel}</QuietTag>
              </Stack>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Try it now with its sample agent, or use it with one of your
                agents.
              </Typography>
            </Box>
            <IconButton
              size="small"
              onClick={onClose}
              aria-label="Close"
              sx={{ flexShrink: 0 }}
            >
              <Iconify icon="solar:close-linear" width={20} />
            </IconButton>
          </Stack>
          <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
            <TemplateBuildPanel key={world.id} template={world.template} />
          </Box>
        </Box>
      )}
    </SideDrawer>
  );
}
WorldDrawer.propTypes = {
  world: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
