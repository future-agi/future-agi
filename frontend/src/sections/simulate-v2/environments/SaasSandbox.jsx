import PropTypes from "prop-types";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { sandboxServices } from "./landingData";
import { QuietTag } from "./LandingBits";
import { PANEL_SX, focusRing } from "./landingStyles";

/*
  The SaaS sandbox: your own agent calls the apps it really uses — Slack,
  Gmail, Notion… — and a sandbox answers like the service and resets between
  runs. Not one more made-up world: in Build environment it's one row under
  the featured worlds; in the full worlds list it's pinned first as a card.
*/

function ServiceTile({ service, size = 26 }) {
  const theme = useTheme();
  return (
    <Tooltip arrow title={service.name}>
      <Box
        aria-label={service.name}
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
        <Iconify
          icon={
            theme.palette.mode === "dark" && service.iconDark
              ? service.iconDark
              : service.icon
          }
          width={Math.round(size * 0.58)}
        />
      </Box>
    </Tooltip>
  );
}
ServiceTile.propTypes = {
  service: PropTypes.object.isRequired,
  size: PropTypes.number,
};

/*
  One compact row under the featured worlds in Build environment — visible
  without scrolling, and plainly second to the worlds above it.
*/
export default function SaasSandboxRow() {
  const navigate = useNavigate();
  const services = useMemo(() => sandboxServices(), []);
  const shown = services.slice(0, 6);
  const open = () => navigate(paths.dashboard.simulate.environmentNewTwin);
  return (
    <ButtonBase
      onClick={open}
      sx={{
        ...PANEL_SX,
        width: "100%",
        px: 1.5,
        py: 1,
        display: "flex",
        alignItems: "center",
        gap: 2,
        textAlign: "left",
        transition: "border-color .12s ease",
        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
        "&:hover .sandbox-go": { color: "text.primary" },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      {/* The apps first: they say what this is before the words do. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ flexShrink: 0 }}
      >
        {shown.map((sv) => (
          <ServiceTile key={sv.id} service={sv} size={24} />
        ))}
      </Stack>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography noWrap sx={{ typography: "s2_1", fontWeight: 600 }}>
            SaaS sandbox
          </Typography>
          <QuietTag>Any agent</QuietTag>
        </Stack>
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {`Your own agent on the real apps it calls — ${services.length} apps, answered by a sandbox that resets between runs.`}
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
          typography: "s2",
          fontWeight: 600,
        }}
      >
        <span>Set up sandbox</span>
        <Iconify icon="solar:arrow-right-linear" width={14} />
      </Stack>
    </ButtonBase>
  );
}

/** The same, as a card — pinned first in the full worlds list. */
export function SaasSandboxCard() {
  const navigate = useNavigate();
  const services = useMemo(() => sandboxServices(), []);
  const shown = services.slice(0, 7);
  return (
    <ButtonBase
      onClick={() => navigate(paths.dashboard.simulate.environmentNewTwin)}
      sx={{
        ...PANEL_SX,
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        textAlign: "left",
        transition: "border-color .12s ease",
        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
        "&.Mui-focusVisible": focusRing,
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
          sx={{ typography: "s2_1", fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          SaaS sandbox
        </Typography>
        <QuietTag>Any agent</QuietTag>
      </Stack>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          mt: 0.5,
          mb: 1.5,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        Your agent calls the real apps — the sandbox answers and resets between
        runs
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          mt: "auto",
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {shown.map((sv) => (
          <ServiceTile key={sv.id} service={sv} size={22} />
        ))}
        {services.length > shown.length && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", pl: 0.25 }}
          >{`+${services.length - shown.length}`}</Typography>
        )}
      </Stack>
    </ButtonBase>
  );
}
