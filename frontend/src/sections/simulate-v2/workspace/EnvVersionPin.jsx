import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Menu, MenuItem, ListItemIcon, ListItemText, Chip, Divider, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { environmentVersions, currentEnvVersion, nextEnvVersion } from "../_mock/versions";
import { INVALIDATING } from "../_mock/proofs";

/**
 * The environment-version pin.
 *
 * The workspace header now carries which version of the world you are
 * looking at, and clicking it opens the full version list so you can
 * switch. Runs stamp whichever version is active when they start, so
 * "agent v2 × env v3" and "agent v2 × env v1" are two rows that a
 * comparison can hold against each other.
 *
 * Two visual states:
 *   - When the active version is the newest, the pin reads "env v3" in
 *     the neutral outline. Nothing unusual is happening.
 *   - When the active version is older, it flips to an amber tint with a
 *     "not latest" badge — because editing off an older version is a
 *     valid thing to do (rollback, comparing worlds) but not the default,
 *     so the header carries that fact instead of hiding it.
 */
export default function EnvVersionPin({ env, envState, patch }) {
  const [anchor, setAnchor] = useState(null);
  const versions = environmentVersions(env, envState);
  const active = currentEnvVersion(env, envState);
  const newest = versions[0];
  const onOldest = active.label !== newest?.label;

  const switchTo = (label) => {
    patch({ activeEnvVersion: label });
    setAnchor(null);
  };

  /*
    Nikhil's feedback (transcript): the env is the frozen asset;
    most iteration happens on agents / scenarios / evals while the
    env stays put. But if the source *did* move, users need a way
    to re-derive — and that affordance should exist, just not as
    the centerpiece. It lives here inside the version menu because
    this is where env-version things live. Mints a new env version
    tagged "Re-derived from source" and makes it active. World-
    invalidating (seed + checks + contract) so proofs against
    prior versions carry the marker.
  */
  const refreshFromSource = () => {
    const list = envState?.envVersions?.length
      ? envState.envVersions
      : [...environmentVersions(env, envState)].reverse();
    const version = nextEnvVersion(env, envState, {
      changed: ["seed", "checks", "contract"],
      note: "Re-derived from source",
    });
    patch({
      envVersions: [...list, version],
      activeEnvVersion: version.label,
    });
    setAnchor(null);
  };

  return (
    <>
      {/*
        Colour discipline: neutral when active is the latest. Green was
        colliding with the "Live" chip beside it and the "Ready" pill on
        the environments table — three greens, three meanings. Amber
        stays reserved for the actual anomaly (editing off latest).
      */}
      <Stack
        direction="row" alignItems="center" spacing={0.625}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{
          px: 0.875, height: 22, borderRadius: 0.75, cursor: "pointer",
          border: "1px solid",
          color: onOldest ? "#CA8A04" : "text.secondary",
          borderColor: (t) => onOldest
            ? alpha("#CA8A04", 0.35)
            : t.palette.divider,
          bgcolor: (t) => onOldest
            ? alpha("#CA8A04", t.palette.mode === "dark" ? 0.14 : 0.08)
            : "transparent",
          "&:hover": {
            borderColor: (t) => onOldest
              ? alpha("#CA8A04", 0.55)
              : t.palette.text.disabled,
          },
        }}
      >
        <Iconify icon="solar:code-linear" width={11} sx={{ opacity: 0.75 }} />
        <Typography sx={{ typography: "s3", fontWeight: 700 }}>
          env {active.label}
        </Typography>
        {onOldest && (
          <Typography sx={{ typography: "s3", fontWeight: 600, opacity: 0.85 }}>
            · not latest
          </Typography>
        )}
        <Iconify icon="solar:alt-arrow-down-linear" width={10} sx={{ opacity: 0.65 }} />
      </Stack>

      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 340, maxWidth: 460 } } }}
      >
        <Box sx={{ px: 2, pt: 1.5, pb: 0.75 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Environment versions
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            A run pins whichever version is active when it starts.
          </Typography>
        </Box>

        {versions.map((v) => {
          const isActive = v.label === active.label;
          const isNewest = v.label === newest.label;
          const changed = (v.changed || []).filter((c) => INVALIDATING[c]);
          return (
            <MenuItem
              key={v.label}
              onClick={() => switchTo(v.label)}
              selected={isActive}
              sx={{ alignItems: "flex-start", py: 1, gap: 1 }}
            >
              <ListItemIcon sx={{ minWidth: 24, mt: "3px" }}>
                {isActive ? (
                  <Iconify icon="solar:check-circle-bold" width={14} sx={{ color: "#7857FC" }} />
                ) : (
                  <Box sx={{ width: 14 }} />
                )}
              </ListItemIcon>
              <ListItemText
                primary={(
                  <Stack direction="row" alignItems="center" spacing={0.625} flexWrap="wrap" rowGap={0.25}>
                    <Typography sx={{ typography: "s2", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                      {v.label}
                    </Typography>
                    {isNewest && (
                      <Chip
                        size="small" label="latest"
                        sx={{
                          height: 16, borderRadius: 0.5, color: "text.subtitle",
                          border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                          "& .MuiChip-label": { px: 0.5, typography: "s3", fontWeight: 600 },
                        }}
                      />
                    )}
                    {isActive && (
                      <Chip
                        size="small" label="active"
                        sx={{
                          height: 16, borderRadius: 0.5, color: "#7857FC",
                          border: "1px solid", borderColor: (t) => alpha("#7857FC", 0.4), bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
                          "& .MuiChip-label": { px: 0.5, typography: "s3", fontWeight: 700 },
                        }}
                      />
                    )}
                  </Stack>
                )}
                secondary={(
                  <Stack spacing={0.25} sx={{ mt: 0.25 }}>
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                      {v.note}
                    </Typography>
                    {changed.length > 0 && (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                        {changed.map((c) => c).join(" · ")} · {v.scenarios} scenarios
                      </Typography>
                    )}
                  </Stack>
                )}
                primaryTypographyProps={{ component: "div" }}
                secondaryTypographyProps={{ component: "div" }}
              />
            </MenuItem>
          );
        })}

        {/*
          De-emphasized "Refresh from source" action at the bottom of
          the version menu. Nikhil's ask: not a centerpiece — a quiet
          affordance for the rare case the source has moved. Reads as
          "re-derive a fresh version" not "reset the world".
        */}
        <Divider sx={{ my: 0.5 }} />
        <Tooltip
          arrow placement="left"
          title="Re-reads the source agent and rebuilds tools, rules and scenarios. Existing versions are kept — a new env version is minted and set active."
        >
          <MenuItem onClick={refreshFromSource} sx={{ py: 1, gap: 1 }}>
            <ListItemIcon sx={{ minWidth: 24 }}>
              <Iconify icon="solar:refresh-linear" width={14} sx={{ color: "text.subtitle" }} />
            </ListItemIcon>
            <ListItemText
              primary={(
                <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
                  Refresh from source
                </Typography>
              )}
              secondary={(
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  Re-derive tools, rules and scenarios; mint a new version
                </Typography>
              )}
              primaryTypographyProps={{ component: "div" }}
              secondaryTypographyProps={{ component: "div" }}
            />
          </MenuItem>
        </Tooltip>
      </Menu>
    </>
  );
}

EnvVersionPin.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
};
