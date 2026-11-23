import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import { BTN_SX, PANEL_SX, T12, T13, focusRing } from "../drawerStyles";
import { QuietTag } from "./startBits";

const clampSx = {
  ...T12,
  color: "text.secondary",
  mt: 0.5,
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

/*
  A ready environment as the design's card shows it: what it tests, its scenarios and
  tools, and the two ways in, Use with my agent and Customize. The card itself opens
  the environment on its World tab.
*/
export default function ReadyEnvironmentCard({ world, onOpen }) {
  return (
    <Box
      component="li"
      aria-label={world.name}
      sx={{
        ...PANEL_SX,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        transition: "border-color .12s ease",
        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
      }}
    >
      <ButtonBase
        onClick={() => onOpen(world, "world")}
        aria-label={`Open ${world.name}`}
        sx={{
          p: 1.5,
          pb: 1.25,
          display: "block",
          textAlign: "left",
          flex: 1,
          borderRadius: "inherit",
          "&.Mui-focusVisible": focusRing,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1} minWidth={0}>
          <Typography
            noWrap
            sx={{ ...T13, fontWeight: 600, flex: 1, minWidth: 0 }}
          >
            {world.name}
          </Typography>
          <QuietTag>{world.typeLabel}</QuietTag>
        </Stack>
        <Typography sx={clampSx}>{world.tagline}</Typography>
        <Typography
          noWrap
          sx={{
            ...T12,
            color: "text.secondary",
            mt: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${world.scenarioCount} scenarios · ${world.tools} tools`}
        </Typography>
      </ButtonBase>
      <Stack
        direction="row"
        spacing={1}
        sx={{ px: 1.5, pb: 1.5, pt: 0.25 }}
      >
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => onOpen(world, "start")}
          sx={{ ...BTN_SX, flex: 1, whiteSpace: "nowrap" }}
        >
          Use with my agent
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => onOpen(world, "customize")}
          sx={{ ...BTN_SX, borderColor: "divider" }}
        >
          Customize
        </Button>
      </Stack>
    </Box>
  );
}
ReadyEnvironmentCard.propTypes = {
  world: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
};
