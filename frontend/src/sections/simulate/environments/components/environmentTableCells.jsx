import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { MODALITY, UNIDENTIFIED_MODALITY } from "../agentTypes";
import { NOT_PROVIDED } from "../myEnvironments.constants";

const NUM_SX = {
  typography: "s2",
  color: "text.secondary",
  fontVariantNumeric: "tabular-nums",
};

const AGENT_ICON_SX = {
  width: 22,
  height: 22,
  borderRadius: "4px",
  flexShrink: 0,
  display: "grid",
  placeItems: "center",
  bgcolor: "background.neutral",
  color: "text.secondary",
};

const DASH = "-";

const Dash = () => (
  <Typography sx={{ typography: "s2", color: "text.disabled" }}>
    {DASH}
  </Typography>
);

const isMissing = (value) => value == null || value === NOT_PROVIDED;

export const NumberCell = ({ getValue }) => {
  const value = getValue?.();
  if (isMissing(value)) return <Dash />;
  return <Typography sx={NUM_SX}>{value}</Typography>;
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
        <Iconify icon={m?.icon} width={14} />
      </Box>
      <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
        {m?.label}
      </Typography>
    </Stack>
  );
};
AgentTypeCell.propTypes = { getValue: PropTypes.func };

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
        <CustomTooltip
          show={Boolean(description)}
          size="small"
          arrow
          title={description || ""}
          enterDelay={500}
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
        </CustomTooltip>
        {!isMissing(envVersion) && (
          <CustomTooltip show size="small" arrow title="Environment version">
            <Typography
              component="span"
              sx={{
                typography: "s3",
                color: "text.tertiary",
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {envVersion}
            </Typography>
          </CustomTooltip>
        )}
      </Stack>
    </Box>
  );
};
NameCell.propTypes = { row: PropTypes.object };

export const AgentCell = ({ row }) => {
  const agent = row?.original?.agent;
  if (isMissing(agent) || !agent.name) return <Dash />;
  return (
    <Typography
      noWrap
      sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
    >
      {agent.name}
    </Typography>
  );
};
AgentCell.propTypes = { row: PropTypes.object };

export const LastRunCell = ({ row }) => {
  const lastRun = row?.original?.lastRun;
  if (isMissing(lastRun) || lastRun.rate == null) return <Dash />;
  const title =
    lastRun.ordinal != null
      ? `Run ${lastRun.ordinal} · scenarios passed`
      : "Scenarios passed";
  return (
    <CustomTooltip show size="small" arrow title={title}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: "fontWeightSemiBold",
          fontVariantNumeric: "tabular-nums",
          cursor: "default",
        }}
      >
        {`${lastRun.rate}%`}
      </Typography>
    </CustomTooltip>
  );
};
LastRunCell.propTypes = { row: PropTypes.object };
