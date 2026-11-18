import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { MODALITY, UNIDENTIFIED_MODALITY } from "../agentTypes";
import { ENV_STATUS, NOT_PROVIDED } from "../myEnvironments.constants";

const VALUE_SX = { typography: "s1", color: "text.secondary" };

const NUM_SX = { ...VALUE_SX, fontVariantNumeric: "tabular-nums" };

const DASH = "-";

const Dash = () => (
  <Typography sx={{ typography: "s1", color: "text.disabled" }}>
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
      spacing={0.75}
      sx={{ minWidth: 0 }}
    >
      <Iconify
        icon={m?.icon}
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography noWrap sx={{ ...VALUE_SX, minWidth: 0 }}>
        {m?.label}
      </Typography>
    </Stack>
  );
};
AgentTypeCell.propTypes = { getValue: PropTypes.func };

export const NameCell = ({ row }) => {
  const { name, envVersion } = row?.original || {};
  return (
    <Box sx={{ minWidth: 0, width: "100%" }}>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography
          noWrap
          sx={{
            typography: "s1",
            fontWeight: "fontWeightMedium",
            color: "text.primary",
            minWidth: 0,
          }}
        >
          {name}
        </Typography>
        {!isMissing(envVersion) && (
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
    <Typography noWrap sx={{ ...VALUE_SX, minWidth: 0 }}>
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
      <Typography sx={{ ...NUM_SX, color: "text.primary", cursor: "default" }}>
        {`${lastRun.rate}%`}
      </Typography>
    </CustomTooltip>
  );
};
LastRunCell.propTypes = { row: PropTypes.object };

export const RunButtonCell = ({ row, onRun }) => {
  const env = row?.original;
  if (env?.status !== ENV_STATUS.READY) return null;
  const label = isMissing(env.envVersion) ? "Run" : `Run · ${env.envVersion}`;
  return (
    <Button
      size="small"
      variant="outlined"
      color="inherit"
      onClick={(e) => {
        e.stopPropagation();
        onRun?.(env);
      }}
      sx={{ whiteSpace: "nowrap" }}
    >
      {label}
    </Button>
  );
};
RunButtonCell.propTypes = { row: PropTypes.object, onRun: PropTypes.func };
