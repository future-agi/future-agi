import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { MODALITY, UNIDENTIFIED_MODALITY } from "../agentTypes";

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
  return <Typography sx={NUM_SX}>{value ?? "—"}</Typography>;
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
