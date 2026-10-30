import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography, Popover } from "@mui/material";
import Iconify from "src/components/iconify";
import { fDateTime } from "src/utils/format-time";
import {
  canMakeDefault,
  isVersioningDisabled,
  useEnvironmentVersions,
  useMoveDefault,
} from "src/api/simulate-environments/versions";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { VERSIONS_COPY, WORKSPACE_COPY } from "./workspace.constants";
import { refusalText } from "./evals/refusalText";
import MakeDefaultConfirm from "./versions/MakeDefaultConfirm";

const ACCENT = BUILD_TONES.accent;

// The environment-version pin: the header names the default environment version,
// and clicking it lists every version of the world, where an earlier active one
// can be made the default. Shown once the server has answered (a failed refresh
// keeps the last answer); hidden when versioning is off, has just been switched
// off, or the environment has no versions.
export default function EnvVersionPin({ env }) {
  const [anchor, setAnchor] = useState(null);
  // The version waiting on the user's confirmation.
  const [confirming, setConfirming] = useState(null);
  const move = useMoveDefault(env?.id);
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  const versions = data?.environment || [];
  const current = data?.defaults?.environment;

  if (!enabled || !data || isVersioningDisabled(error) || !current) {
    return null;
  }
  const currentLabel = current.label || VERSIONS_COPY.unlabelled;

  const open = (e) => setAnchor(e.currentTarget);
  const close = () => {
    setAnchor(null);
    move.reset();
  };
  const { pin, versionBar } = WORKSPACE_COPY;
  const { makeDefault } = VERSIONS_COPY;
  const defaultIndex = versions.findIndex((v) => v.id === current.id);
  // Versions are newest first, so one listed after the default is older.
  const actionLabel = (v, index) => {
    const label = v.label || VERSIONS_COPY.unlabelled;
    return index > defaultIndex
      ? pin.rollBack(label)
      : makeDefault.actionFor(label);
  };
  const confirmMove = () =>
    move.mutate(
      {
        piece: "environment",
        versionId: confirming.id,
        expectedDefaultId: current.id,
      },
      { onSettled: () => setConfirming(null) },
    );

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.625}
        role="button"
        tabIndex={0}
        aria-label={`${versionBar.envPrefix} ${currentLabel}`}
        aria-haspopup="dialog"
        aria-expanded={Boolean(anchor)}
        aria-controls={anchor ? "env-version-list" : undefined}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open(e);
          }
        }}
        sx={{
          px: 0.875,
          height: 22,
          borderRadius: 0.75,
          cursor: "pointer",
          border: "1px solid",
          color: "text.secondary",
          borderColor: "divider",
          bgcolor: "transparent",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        <Iconify icon="solar:code-linear" width={11} sx={{ opacity: 0.75 }} />
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>
          {versionBar.envPrefix} {currentLabel}
        </Typography>
        <Iconify
          data-testid="env-version-chevron"
          icon="solar:alt-arrow-down-linear"
          width={10}
          sx={{ opacity: 0.65 }}
        />
      </Stack>

      <Popover
        id="env-version-list"
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            role: "dialog",
            "aria-labelledby": "env-version-list-heading",
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
        }}
      >
        <Box sx={{ px: 1.25, pt: 0.75, pb: 1 }}>
          <Typography
            id="env-version-list-heading"
            sx={{
              typography: "s3",
              fontWeight: "fontWeightBold",
              color: "text.subtitle",
              textTransform: "uppercase",
              letterSpacing: 0.5,
            }}
          >
            {pin.heading}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {pin.subtitle}
          </Typography>
          {move.isError && (
            <Typography
              role="alert"
              sx={{ typography: "s3", color: "error.main", mt: 0.75 }}
            >
              {refusalText(move.error, makeDefault.error)}
            </Typography>
          )}
        </Box>

        <Stack
          spacing={0.5}
          component="ul"
          aria-labelledby="env-version-list-heading"
          sx={{ listStyle: "none", m: 0, p: 0 }}
        >
          {versions.map((v, index) => (
            <Box
              component="li"
              key={v.id}
              data-testid="env-version-item"
              sx={{
                px: 1.25,
                py: 1.125,
                borderRadius: 1,
                border: "1px solid",
                borderColor: v.isDefault ? alpha(ACCENT, 0.35) : "transparent",
                bgcolor: (t) =>
                  v.isDefault ? alpha(ACCENT, t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent",
              }}
            >
              <Stack direction="row" alignItems="center" spacing={0.625} flexWrap="wrap" rowGap={0.375}>
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: "fontWeightBold",
                    fontFamily: "ui-monospace, Menlo, monospace",
                  }}
                >
                  {v.label || VERSIONS_COPY.unlabelled}
                </Typography>
                {v.isDefault && <VersionBadge label={pin.default} tone={ACCENT} />}
                {VERSIONS_COPY.status[v.status] && (
                  <VersionBadge label={VERSIONS_COPY.status[v.status]} />
                )}
              </Stack>
              {v.summary && (
                <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.375 }}>
                  {v.summary}
                </Typography>
              )}
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  mt: 0.25,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {[fDateTime(v.createdAt), v.createdBy && VERSIONS_COPY.by(v.createdBy)]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
              {canMakeDefault("environment", v) && (
                <Button
                  size="small"
                  variant="outlined"
                  color="inherit"
                  disabled={move.isPending}
                  onClick={() => setConfirming(v)}
                  sx={{ mt: 0.75, height: 24, textTransform: "none" }}
                >
                  {actionLabel(v, index)}
                </Button>
              )}
            </Box>
          ))}
        </Stack>
      </Popover>
      <MakeDefaultConfirm
        piece="environment"
        version={confirming}
        pending={move.isPending}
        onConfirm={confirmMove}
        onClose={() => setConfirming(null)}
      />
    </>
  );
}

EnvVersionPin.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }).isRequired,
};

// Small pill — neutral outline by default, tinted when a tone is given.
export function VersionBadge({ label, tone }) {
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
