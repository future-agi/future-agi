import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  Tooltip,
  Popover,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * "Also run in" — the quiet way to turn one run into a run group.
 *
 * Lists the other environments testing the same agent as checkable chips.
 * Nothing picked is the ordinary single-environment run, unchanged; each one
 * picked adds a run of the same agent version in that environment, started
 * with this one and read beside it.
 *
 *   variant "button"  an outlined trigger beside the Run button, same shape as
 *                     the Repeats picker, opening the chips in a popover
 *   variant "inline"  the chips on their own, for a surface that is already a
 *                     popover (Run again)
 *
 * Renders nothing when no other environment tests this agent.
 */
export default function AlsoRunIn({
  candidates = [],
  value = [],
  onChange,
  agentVersion,
  disabled = false,
  disabledReason = "",
  variant = "button",
  hideLabel = false,
}) {
  const [anchor, setAnchor] = useState(null);
  if (!candidates.length) return null;

  const toggle = (id) =>
    onChange?.(
      value.includes(id) ? value.filter((x) => x !== id) : [...value, id],
    );
  const picked = value.filter((id) =>
    candidates.some((c) => c.envId === id && c.ready),
  );

  const chips = (
    <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
      {candidates.map((c) => (
        <EnvChip
          key={c.envId}
          candidate={c}
          on={picked.includes(c.envId)}
          onToggle={() => toggle(c.envId)}
        />
      ))}
    </Stack>
  );

  if (variant === "inline") {
    return (
      <Box>
        {!hideLabel && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}
          >
            Also run in{agentVersion ? ` · agent ${agentVersion} in each` : ""}
          </Typography>
        )}
        {chips}
      </Box>
    );
  }

  return (
    <>
      <Tooltip
        arrow
        title={
          disabled
            ? disabledReason
            : "Run the same agent version in other environments too, as a group"
        }
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled={disabled}
            onClick={(e) => setAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:layers-linear" width={14} />}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              minWidth: 0,
              whiteSpace: "nowrap",
              "&:hover": {
                borderColor: (t) => alpha(t.palette.text.primary, 0.4),
                bgcolor: "action.hover",
              },
            }}
          >
            {picked.length && !disabled
              ? `+${picked.length} environment${picked.length === 1 ? "" : "s"}`
              : "Also run in"}
          </Button>
        </span>
      </Tooltip>

      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 320, mt: 0.5 } } }}
      >
        <Box
          sx={{
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", fontWeight: 600, color: "text.primary" }}
          >
            Also run in
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontSize: 12,
              mt: 0.25,
            }}
          >
            {agentVersion ? `Agent ${agentVersion}` : "The same agent version"}{" "}
            in each — one run per environment, started together and read side by
            side.
          </Typography>
        </Box>
        <Box sx={{ px: 1.5, py: 1.25 }}>{chips}</Box>
        {picked.length > 0 && (
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.875,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
            >
              {picked.length + 1} runs · this environment and {picked.length}{" "}
              more
            </Typography>
            <ButtonBase
              onClick={() => onChange?.([])}
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.secondary",
                border: 0,
                bgcolor: "transparent",
                cursor: "pointer",
                p: 0,
                "&:hover": { color: "text.primary" },
              }}
            >
              Clear
            </ButtonBase>
          </Stack>
        )}
      </Popover>
    </>
  );
}

AlsoRunIn.propTypes = {
  candidates: PropTypes.array,
  value: PropTypes.array,
  onChange: PropTypes.func,
  agentVersion: PropTypes.string,
  hideLabel: PropTypes.bool,
  disabled: PropTypes.bool,
  disabledReason: PropTypes.string,
  variant: PropTypes.oneOf(["button", "inline"]),
};

/* One environment: its name, and the env version the run would pin. One that
   can't run the same agent version yet stays listed, disabled, saying why. */
function EnvChip({ candidate, on, onToggle }) {
  const chip = (
    <ButtonBase
      type="button"
      disabled={!candidate.ready}
      onClick={onToggle}
      aria-pressed={on}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        height: 26,
        px: 0.875,
        borderRadius: "4px",
        maxWidth: "100%",
        border: "1px solid",
        borderColor: on ? "text.disabled" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
        color: "text.primary",
        cursor: candidate.ready ? "pointer" : "default",
        opacity: candidate.ready ? 1 : 0.5,
        font: "inherit",
        "&:hover": candidate.ready
          ? {
              bgcolor: on ? "action.selected" : "action.hover",
              borderColor: "text.disabled",
            }
          : {},
      }}
    >
      <Iconify
        icon={on ? "solar:check-linear" : "solar:add-linear"}
        width={12}
        sx={{ color: on ? "text.primary" : "text.tertiary", flexShrink: 0 }}
      />
      <Typography
        noWrap
        sx={{ typography: "s3", fontWeight: on ? 600 : 500, minWidth: 0 }}
      >
        {candidate.envName}
      </Typography>
      {(candidate.envShown || candidate.envVersion) && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >
          environment {candidate.envShown || candidate.envVersion}
        </Typography>
      )}
    </ButtonBase>
  );
  return candidate.ready ? (
    chip
  ) : (
    <Tooltip arrow title={candidate.reason}>
      <span style={{ display: "inline-flex", maxWidth: "100%" }}>{chip}</span>
    </Tooltip>
  );
}

EnvChip.propTypes = {
  candidate: PropTypes.object.isRequired,
  on: PropTypes.bool,
  onToggle: PropTypes.func,
};
