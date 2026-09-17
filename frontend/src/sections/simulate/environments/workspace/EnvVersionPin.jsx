import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Menu } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";
import {
  environmentVersions,
  currentEnvVersion,
} from "src/api/simulate-environments/_fixtures/versions";

const ACCENT = BUILD_TONES.accent;

// The environment-version pin. The workspace header carries which version of
// the world you are looking at; clicking it opens the version list so you can
// switch. Runs stamp whichever version is active when they start, so a pin is
// how "agent v2 x env v3" and "agent v2 x env v1" become two comparable rows.
//
// Neutral when the active version is the newest. readOnly for a template-seeded
// env: no chevron, no menu — the reader forks first.
export default function EnvVersionPin({ env, envState, patch, readOnly = false }) {
  const [anchor, setAnchor] = useState(null);
  const versions = environmentVersions(env, envState);
  const active = currentEnvVersion(env, envState);
  const newest = versions[0];

  const open = (e) => setAnchor(e.currentTarget);
  const close = () => setAnchor(null);
  const switchTo = (label) => {
    patch({ activeEnvVersion: label });
    close();
  };

  const interactive = !readOnly
    ? {
        role: "button",
        tabIndex: 0,
        onClick: open,
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open(e);
          }
        },
      }
    : {};

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.625}
        {...interactive}
        sx={{
          px: 0.875,
          height: 22,
          borderRadius: 0.75,
          cursor: readOnly ? "default" : "pointer",
          border: "1px solid",
          color: "text.secondary",
          borderColor: "divider",
          bgcolor: "transparent",
          "&:hover": readOnly ? undefined : { borderColor: "text.disabled" },
        }}
      >
        <Iconify icon="solar:code-linear" width={11} sx={{ opacity: 0.75 }} />
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>
          {WORKSPACE_COPY.versionBar.envPrefix} {active.label}
        </Typography>
        {!readOnly && (
          <Iconify
            data-testid="env-version-chevron"
            icon="solar:alt-arrow-down-linear"
            width={10}
            sx={{ opacity: 0.65 }}
          />
        )}
      </Stack>

      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 336,
              maxWidth: 420,
              mt: 0.5,
              p: 0.75,
              borderRadius: 1.5,
              border: "1px solid",
              borderColor: "divider",
              boxShadow: (t) =>
                `0 12px 32px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.5 : 0.16)}`,
            },
          },
          list: { sx: { p: 0 } },
        }}
      >
        <Box sx={{ px: 1.25, pt: 0.75, pb: 1 }}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightBold",
              color: "text.subtitle",
              textTransform: "uppercase",
              letterSpacing: 0.5,
            }}
          >
            {WORKSPACE_COPY.pin.heading}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {WORKSPACE_COPY.pin.subtitle}
          </Typography>
        </Box>

        <Stack spacing={0.5}>
          {versions.map((v) => {
            const isActive = v.label === active.label;
            const isNewest = v.label === newest.label;
            const pick = () => switchTo(v.label);
            return (
              <Box
                key={v.label}
                role="button"
                tabIndex={0}
                onClick={pick}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    pick();
                  }
                }}
                sx={{
                  px: 1.25,
                  py: 1.125,
                  borderRadius: 1,
                  cursor: "pointer",
                  border: "1px solid",
                  borderColor: isActive ? alpha(ACCENT, 0.35) : "transparent",
                  bgcolor: (t) =>
                    isActive ? alpha(ACCENT, t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent",
                  transition: "background-color .12s ease, border-color .12s ease",
                  "&:hover": { bgcolor: isActive ? undefined : "action.hover" },
                }}
              >
                <Stack direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify
                    icon={isActive ? "solar:check-circle-bold" : "solar:circle-linear"}
                    width={16}
                    sx={{ color: isActive ? ACCENT : "text.disabled", flexShrink: 0, mt: "1px" }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={0.625}
                      flexWrap="wrap"
                      rowGap={0.375}
                    >
                      <Typography
                        sx={{
                          typography: "s2",
                          fontWeight: "fontWeightBold",
                          fontFamily: "ui-monospace, Menlo, monospace",
                        }}
                      >
                        {v.label}
                      </Typography>
                      {isNewest && <VersionBadge label={WORKSPACE_COPY.pin.latest} />}
                      {isActive && <VersionBadge label={WORKSPACE_COPY.pin.active} tone={ACCENT} />}
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.375 }}>
                      {v.note}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        mt: 0.25,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {WORKSPACE_COPY.pin.scenarios(v.scenarios)}
                    </Typography>
                  </Box>
                </Stack>
              </Box>
            );
          })}
        </Stack>
      </Menu>
    </>
  );
}

EnvVersionPin.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string }).isRequired,
  envState: PropTypes.shape({
    envVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
    activeEnvVersion: PropTypes.string,
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }).isRequired,
  patch: PropTypes.func.isRequired,
  readOnly: PropTypes.bool,
};

// Small pill — neutral outline by default, tinted when a tone is given.
function VersionBadge({ label, tone }) {
  return (
    <Box
      sx={{
        px: 0.625,
        height: 16,
        borderRadius: 0.5,
        display: "inline-flex",
        alignItems: "center",
        border: "1px solid",
        borderColor: (t) => (tone ? alpha(tone, 0.4) : t.palette.divider),
        color: tone || "text.subtitle",
        bgcolor: (t) => (tone ? alpha(tone, t.palette.mode === "dark" ? 0.14 : 0.08) : "transparent"),
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>{label}</Typography>
    </Box>
  );
}
VersionBadge.propTypes = { label: PropTypes.string, tone: PropTypes.string };
