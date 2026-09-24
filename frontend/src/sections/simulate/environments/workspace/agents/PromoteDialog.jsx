import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Dialog, DialogTitle, DialogContent, DialogActions } from "@mui/material";

import Iconify from "src/components/iconify";

import { MODALITY } from "../../agentTypes";
import { STEP_FALLBACKS } from "./agents.constants";
import { AGENT_SHAPE } from "./agents.shapes";
import { AGENT_CARD_COPY, PROMOTE_IMPACT_ROWS } from "./agentCards.constants";
import { ImpactRow } from "./agentPrimitives";

// Confirmation for promoting an additional agent to source. Spells out what
// re-reading the contract from this agent changes before the commitment.
export default function PromoteDialog({ agent, onCancel, onConfirm }) {
  const typeLabel = MODALITY[agent?.typeId]?.label || STEP_FALLBACKS.agent;
  return (
    <Dialog open={!!agent} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
        {AGENT_CARD_COPY.promoteTitle}
      </DialogTitle>
      <DialogContent>
        <Typography sx={{ typography: "s2", color: "text.secondary", mb: 2 }}>
          {AGENT_CARD_COPY.promoteIntro}{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace", color: "text.primary" }}>
            {typeLabel}
          </Box>
          {AGENT_CARD_COPY.promoteImpactsLead}
        </Typography>
        <Stack spacing={1} sx={{ mb: 2 }}>
          {PROMOTE_IMPACT_ROWS.map((row) => (
            <ImpactRow key={row.title} icon={row.icon} title={row.title} body={row.body} />
          ))}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
          {AGENT_CARD_COPY.cancel}
        </Button>
        <Button
          variant="contained" color="primary" onClick={onConfirm}
          startIcon={<Iconify icon="solar:refresh-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
        >
          {AGENT_CARD_COPY.updateEnvironment}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
PromoteDialog.propTypes = {
  agent: AGENT_SHAPE, onCancel: PropTypes.func, onConfirm: PropTypes.func,
};
