import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Menu } from "@mui/material";
import Iconify from "src/components/iconify";
import { environmentVersions, currentEnvVersion } from "../_mock/versions";
import { INVALIDATING } from "../_mock/proofs";

const ACCENT = "#7857FC";

/**
 * The environment-version pin.
 *
 * The workspace header carries which version of the world you are looking at,
 * and clicking it opens the version list so you can switch. Runs stamp
 * whichever version is active when they start, so "agent v2 × env v3" and
 * "agent v2 × env v1" are two rows a comparison can hold against each other.
 *
 * Neutral when the active version is the newest; amber "not latest" when an
 * older version is pinned — a valid thing to do (rollback / comparing worlds),
 * surfaced rather than hidden.
 */
export default function EnvVersionPin({ env, envState, patch }) {
  const [anchor, setAnchor] = useState(null);
  const versions = environmentVersions(env, envState);
  const active = currentEnvVersion(env, envState);
  const newest = versions[0];

  const switchTo = (label) => {
    patch({ activeEnvVersion: label });
    setAnchor(null);
  };

  return (
    <>
      {/*
        Colour discipline: neutral when active is the latest. Amber stays
        reserved for the actual anomaly (editing off latest).
      */}
      <Stack
        direction="row" alignItems="center" spacing={0.625}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{
          px: 0.875, height: 22, borderRadius: 0.75, cursor: "pointer",
          border: "1px solid",
          color: "text.secondary",
          borderColor: "divider",
          bgcolor: "transparent",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        <Iconify icon="solar:code-linear" width={11} sx={{ opacity: 0.75 }} />
        <Typography sx={{ typography: "s3", fontWeight: 700 }}>env {active.label}</Typography>
        <Iconify icon="solar:alt-arrow-down-linear" width={10} sx={{ opacity: 0.65 }} />
      </Stack>

      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              minWidth: 336, maxWidth: 420, mt: 0.5, p: 0.75, borderRadius: 1.5,
              border: "1px solid", borderColor: "divider",
              boxShadow: (t) => `0 12px 32px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.5 : 0.16)}`,
            },
          },
          list: { sx: { p: 0 } },
        }}
      >
        <Box sx={{ px: 1.25, pt: 0.75, pb: 1 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Environment versions
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            A run pins whichever version is active when it starts.
          </Typography>
        </Box>

        <Stack spacing={0.5}>
          {versions.map((v) => {
            const isActive = v.label === active.label;
            const isNewest = v.label === newest.label;
            const changed = (v.changed || []).filter((c) => INVALIDATING[c]);
            return (
              <Box
                key={v.label}
                role="button"
                onClick={() => switchTo(v.label)}
                sx={{
                  px: 1.25, py: 1.125, borderRadius: 1, cursor: "pointer",
                  border: "1px solid",
                  borderColor: isActive ? alpha(ACCENT, 0.35) : "transparent",
                  bgcolor: (t) => (isActive ? alpha(ACCENT, t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent"),
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
                    <Stack direction="row" alignItems="center" spacing={0.625} flexWrap="wrap" rowGap={0.375}>
                      <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                        {v.label}
                      </Typography>
                      {isNewest && <Badge label="latest" />}
                      {isActive && <Badge label="active" tone={ACCENT} />}
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.375 }}>
                      {v.note}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, fontVariantNumeric: "tabular-nums" }}>
                      {[...changed, `${v.scenarios} scenarios`].join(" · ")}
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
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
};

/* Small pill — neutral outline by default, tinted when a tone is given. */
function Badge({ label, tone }) {
  return (
    <Box
      sx={{
        px: 0.625, height: 16, borderRadius: 0.5, display: "inline-flex", alignItems: "center",
        border: "1px solid",
        borderColor: (t) => (tone ? alpha(tone, 0.4) : t.palette.divider),
        color: tone || "text.subtitle",
        bgcolor: (t) => (tone ? alpha(tone, t.palette.mode === "dark" ? 0.14 : 0.08) : "transparent"),
      }}
    >
      <Typography sx={{ typography: "s3", fontWeight: 700 }}>{label}</Typography>
    </Box>
  );
}
Badge.propTypes = { label: PropTypes.string, tone: PropTypes.string };
