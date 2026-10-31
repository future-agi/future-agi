import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { PANEL_SX, T12, T13, focusRing } from "../drawerStyles";
import { SANDBOX_APPS } from "./sandboxApps";
import { AppMark } from "./SandboxForm";
import { QuietTag } from "./startBits";

const cardSx = {
  ...PANEL_SX,
  width: "100%",
  height: "100%",
  p: 1.5,
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
  textAlign: "left",
  transition: "border-color .12s ease",
  "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
  "&.Mui-focusVisible": focusRing,
};

const clampSx = {
  ...T12,
  color: "text.secondary",
  mt: 0.5,
  mb: 1.5,
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

const footSx = {
  mt: "auto",
  pt: 1.25,
  borderTop: "1px solid",
  borderColor: "divider",
};

/** One prebuilt world. It opens the world's setup; a `soon` one can't. */
export function WorldCard({ world, onOpen, soon = false }) {
  return (
    <Box component="li" aria-label={world.name} sx={{ minWidth: 0 }}>
      <ButtonBase
        onClick={soon ? undefined : () => onOpen(world)}
        disabled={soon}
        aria-label={`Open ${world.name}`}
        sx={{
          ...cardSx,
          ...(soon && { "&:hover": {} }),
          "&.Mui-disabled": { cursor: "default", pointerEvents: "auto" },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{
              ...T13,
              fontWeight: 600,
              flex: 1,
              minWidth: 0,
              color: soon ? "text.secondary" : "text.primary",
            }}
          >
            {world.name}
          </Typography>
          {soon && <QuietTag>Coming soon</QuietTag>}
          <QuietTag>{world.typeLabel}</QuietTag>
        </Stack>
        <Typography sx={clampSx}>{world.tagline}</Typography>
        <Stack
          direction="row"
          justifyContent="space-between"
          spacing={1}
          sx={footSx}
        >
          <Typography
            noWrap
            sx={{
              ...T12,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {`${world.tools} tools · ${world.tables} tables`}
          </Typography>
          <Typography
            noWrap
            sx={{ ...T12, color: "text.secondary", flexShrink: 0 }}
          >
            {world.domainLabel}
          </Typography>
        </Stack>
      </ButtonBase>
    </Box>
  );
}
WorldCard.propTypes = {
  world: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
  soon: PropTypes.bool,
};

function AppTile({ app, size }) {
  return (
    <CustomTooltip show arrow size="small" title={app.name}>
      <Box
        aria-label={app.name}
        sx={{
          width: size,
          height: size,
          borderRadius: "4px",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
          bgcolor: (t) => alpha(t.palette.text.primary, 0.05),
        }}
      >
        <AppMark app={app} width={Math.round(size * 0.58)} />
      </Box>
    </CustomTooltip>
  );
}
AppTile.propTypes = {
  app: PropTypes.object.isRequired,
  size: PropTypes.number.isRequired,
};

/** The SaaS sandbox as one row under the featured worlds. */
export function SaasSandboxRow({ onOpen }) {
  return (
    <ButtonBase
      onClick={onOpen}
      aria-label="SaaS sandbox"
      sx={{
        ...cardSx,
        height: "auto",
        px: 1.5,
        py: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 2,
        "&:hover .sandbox-go": { color: "text.primary" },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ flexShrink: 0 }}
      >
        {SANDBOX_APPS.slice(0, 6).map((a) => (
          <AppTile key={a.id} app={a} size={24} />
        ))}
      </Stack>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ ...T13, fontWeight: 600 }}>
            SaaS sandbox
          </Typography>
          <QuietTag>Any agent</QuietTag>
          <UiOnlyFlag />
        </Stack>
        <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
          {`Your own agent on the real apps it calls. ${SANDBOX_APPS.length} apps, answered by a sandbox that resets between runs.`}
        </Typography>
      </Box>
      <Stack
        className="sandbox-go"
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          flexShrink: 0,
          color: "text.secondary",
          ...T13,
          fontWeight: 600,
        }}
      >
        <span>Set up sandbox</span>
        <Iconify icon="solar:arrow-right-linear" width={14} />
      </Stack>
    </ButtonBase>
  );
}
SaasSandboxRow.propTypes = { onOpen: PropTypes.func.isRequired };

/** The same, as a card pinned first in the full worlds list. */
export function SaasSandboxCard({ onOpen }) {
  const shown = SANDBOX_APPS.slice(0, 7);
  return (
    <Box component="li" aria-label="SaaS sandbox" sx={{ minWidth: 0 }}>
      <ButtonBase
        onClick={onOpen}
        aria-label="Set up a SaaS sandbox"
        sx={cardSx}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{ ...T13, fontWeight: 600, flex: 1, minWidth: 0 }}
          >
            SaaS sandbox
          </Typography>
          <UiOnlyFlag />
          <QuietTag>Any agent</QuietTag>
        </Stack>
        <Typography sx={clampSx}>
          Your agent calls the real apps. The sandbox answers and resets between
          runs.
        </Typography>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={footSx}>
          {shown.map((a) => (
            <AppTile key={a.id} app={a} size={22} />
          ))}
          {SANDBOX_APPS.length > shown.length && (
            <Typography sx={{ ...T12, color: "text.secondary", pl: 0.25 }}>
              {`+${SANDBOX_APPS.length - shown.length}`}
            </Typography>
          )}
        </Stack>
      </ButtonBase>
    </Box>
  );
}
SaasSandboxCard.propTypes = { onOpen: PropTypes.func.isRequired };
