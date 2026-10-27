import PropTypes from "prop-types";
import { Avatar, Box, Chip, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { MODALITY, UNIDENTIFIED_MODALITY } from "../agentTypes";
import { NOT_PROVIDED } from "../myEnvironments.constants";
import SoonChip from "./SoonChip";

const NUM_SX = {
  typography: "s2",
  color: "text.secondary",
  fontVariantNumeric: "tabular-nums",
};

const AGENT_ICON_SX = {
  width: 22,
  height: 22,
  borderRadius: 0.75,
  flexShrink: 0,
  display: "grid",
  placeItems: "center",
  bgcolor: "background.neutral",
  color: "text.secondary",
};

export const NumberCell = ({ getValue }) => {
  const value = getValue?.();
  // A dummy column (tools/scenarios/sub-goals) has no value in the harness
  // payload; show a dash rather than an empty cell.
  return <Typography sx={NUM_SX}>{value ?? "-"}</Typography>;
};
NumberCell.propTypes = { getValue: PropTypes.func };

export const AgentTypeCell = ({ getValue }) => {
  const m = MODALITY[getValue?.()] || UNIDENTIFIED_MODALITY;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.875}
      sx={{ minWidth: 0 }}
    >
      <Box sx={AGENT_ICON_SX}>
        <Iconify icon={m?.icon} width={13} />
      </Box>
      <Typography noWrap sx={{ typography: "s2" }}>
        {m?.label}
      </Typography>
    </Stack>
  );
};
AgentTypeCell.propTypes = { getValue: PropTypes.func };

const DASH = "-";
const UP = "#16A34A";
const DOWN = "#DC2626";

const Dash = () => (
  <Typography sx={{ typography: "s2", color: "text.disabled" }}>
    {DASH}
  </Typography>
);

/* The name with its version beside it, and what it is on the line below. */
export const NameCell = ({ row }) => {
  const { name, description, envVersion } = row?.original || {};
  return (
    <Box sx={{ minWidth: 0, width: "100%" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            minWidth: 0,
          }}
        >
          {name}
        </Typography>
        {envVersion === NOT_PROVIDED && (
          <SoonChip label="Version" what="Environment version" />
        )}
      </Stack>
      {description && (
        <CustomTooltip
          show
          size="small"
          arrow
          title={description}
          enterDelay={500}
        >
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
          >
            {description}
          </Typography>
        </CustomTooltip>
      )}
    </Box>
  );
};
NameCell.propTypes = { row: PropTypes.object };

/* The agent it tests, and its Main / Live / Sealed tags. */
export const AgentCell = ({ row }) => {
  const { agent, agentTags } = row?.original || {};
  if (agent === NOT_PROVIDED) return <SoonChip label="Agent" what="Agent" />;
  if (!agent) return <Dash />;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ minWidth: 0, width: "100%" }}
    >
      <Typography
        noWrap
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        {agent.name}
      </Typography>
      {agentTags === NOT_PROVIDED && (
        <SoonChip label="Tags" what="Main and Live / Sealed tags" plural />
      )}
    </Stack>
  );
};
AgentCell.propTypes = { row: PropTypes.object };

/* "75% ▲5": the last run's pass rate and how it moved from the run before. */
export const LastRunCell = ({ row }) => {
  const lastRun = row?.original?.lastRun;
  if (lastRun === NOT_PROVIDED) {
    return <SoonChip label="Last run" what="Last run" />;
  }
  if (!lastRun) return <Dash />;
  const { rate, delta } = lastRun;
  const moved = typeof delta === "number" && delta !== 0;
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {`${rate}%`}
      </Typography>
      {moved && (
        <Typography
          sx={{
            typography: "s3",
            fontWeight: "fontWeightSemiBold",
            color: delta > 0 ? UP : DOWN,
          }}
        >
          {`${delta > 0 ? "▲" : "▼"}${Math.abs(delta)}`}
        </Typography>
      )}
      {delta === NOT_PROVIDED && (
        <SoonChip label="Trend" what="Change from the previous run" />
      )}
    </Stack>
  );
};
LastRunCell.propTypes = { row: PropTypes.object };

const initialsOf = (name) => {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  return (
    parts.length >= 2 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)
  ).toUpperCase();
};

/* Who owns it: their initials, their name on hover. */
export const OwnerCell = ({ getValue }) => {
  const owner = getValue?.();
  if (owner === NOT_PROVIDED) return <SoonChip label="Owner" what="Owner" />;
  if (!owner) return <Dash />;
  return (
    <CustomTooltip show size="small" arrow title={owner.name || ""}>
      <Avatar
        sx={{
          width: 26,
          height: 26,
          typography: "s3",
          fontWeight: "fontWeightBold",
        }}
      >
        {initialsOf(owner.name)}
      </Avatar>
    </CustomTooltip>
  );
};
OwnerCell.propTypes = { getValue: PropTypes.func };

/* How many simulations it has run, as a small outlined count. */
export const RunsCell = ({ getValue }) => {
  const total = getValue?.();
  if (total == null) return <Dash />;
  return (
    <Box
      sx={{
        display: "inline-flex",
        px: 0.75,
        py: 0.25,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          fontWeight: "fontWeightBold",
          fontVariantNumeric: "tabular-nums",
          color: "text.secondary",
        }}
      >
        {total}
      </Typography>
    </Box>
  );
};
RunsCell.propTypes = { getValue: PropTypes.func };
