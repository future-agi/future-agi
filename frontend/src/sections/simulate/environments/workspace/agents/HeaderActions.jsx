import PropTypes from "prop-types";
import { Stack, Button } from "@mui/material";

import { AGENT_SHAPE } from "./agents.shapes";
import { SOURCE_ACCENT, AGENT_CARD_COPY } from "./agentCards.constants";
import { ChipButton, ChipIconButton } from "./agentPrimitives";

// The action bar on the right of every card header. Both action tiers live here
// so their relative weight reads on sight:
//  · Set active        — filled chip button. Cheap, reversible flip of which
//                        agent runs execute against.
//  · Promote to source — text link. Rare, destructive commitment that re-derives
//                        the contract; kept clearly subordinate to Set active so
//                        the two don't read as parallel choices.
//  · Remove            — icon-only, right-most.
// stopPropagation on every click so a button doesn't also toggle the row expand.
export default function HeaderActions({ agent, isActive, onSetActive, onPromote, onRemove }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexShrink: 0 }}>
      {!isActive && (
        <ChipButton
          icon="solar:play-circle-linear"
          label={AGENT_CARD_COPY.setActiveForRuns}
          onClick={(e) => { e.stopPropagation(); onSetActive(); }}
        />
      )}
      {!agent.isSource && (
        <Button
          size="small" variant="text"
          onClick={(e) => { e.stopPropagation(); onPromote?.(); }}
          sx={{
            height: 26, minHeight: 26, minWidth: 0, px: 0.75, borderRadius: 1,
            typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.subtitle",
            "&:hover": { color: SOURCE_ACCENT, bgcolor: "transparent", textDecoration: "underline" },
          }}
        >
          {AGENT_CARD_COPY.promoteToSource}
        </Button>
      )}
      <ChipIconButton
        icon="solar:trash-bin-minimalistic-linear"
        tooltip={agent.isSource ? AGENT_CARD_COPY.removeSource : AGENT_CARD_COPY.removeAdditional}
        onClick={(e) => { e.stopPropagation(); onRemove(); }}
      />
    </Stack>
  );
}
HeaderActions.propTypes = {
  agent: AGENT_SHAPE, isActive: PropTypes.bool,
  onSetActive: PropTypes.func, onPromote: PropTypes.func, onRemove: PropTypes.func,
};
