import PropTypes from "prop-types";
import { useEffect, useRef } from "react";
import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY as COPY } from "../workspace.constants";
import HeldButton from "./HeldButton";

const quietSx = { color: "text.primary", borderColor: "divider" };

function Outcome({ icon, iconColor, label, children }) {
  return (
    <>
      <Iconify icon={icon} width={14} sx={{ color: iconColor }} />
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {label}
      </Typography>
      {children}
    </>
  );
}
Outcome.propTypes = {
  icon: PropTypes.string.isRequired,
  iconColor: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

/** One change's decision: Accept or Reject, then the outcome with a way back. */
export default function ChangeDecision({
  decision = null,
  rejectReason = null,
  restoreReason = null,
  onDecide,
}) {
  const rootRef = useRef(null);
  const moveFocus = useRef(false);
  // The pressed button goes with the decision; focus moves to what replaces it.
  const decide = (next) => {
    moveFocus.current = Boolean(
      rootRef.current?.contains(document.activeElement),
    );
    onDecide?.(next);
  };
  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    rootRef.current?.querySelector("button")?.focus();
  }, [decision]);

  const undoSx = { color: "text.tertiary" };
  return (
    <Stack
      ref={rootRef}
      direction="row"
      alignItems="center"
      spacing={decision ? 0.5 : 0.75}
      sx={{ flexShrink: 0 }}
    >
      {decision === "accepted" && (
        <Outcome
          icon="solar:check-circle-linear"
          iconColor="status.success.fg"
          label={COPY.accepted}
        >
          <Button
            size="small"
            color="inherit"
            onClick={() => decide(null)}
            sx={undoSx}
          >
            {COPY.change}
          </Button>
        </Outcome>
      )}
      {decision === "rejected" && (
        <Outcome
          icon="solar:close-circle-linear"
          iconColor="text.tertiary"
          label={COPY.rejected}
        >
          <HeldButton
            size="small"
            color="inherit"
            reason={restoreReason}
            onClick={() => decide(null)}
            sx={undoSx}
          >
            {COPY.restore}
          </HeldButton>
        </Outcome>
      )}
      {!decision && (
        <>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => decide("accepted")}
            sx={quietSx}
          >
            {COPY.accept}
          </Button>
          <HeldButton
            size="small"
            variant="outlined"
            color="inherit"
            reason={rejectReason}
            onClick={() => decide("rejected")}
            sx={quietSx}
          >
            {COPY.reject}
          </HeldButton>
        </>
      )}
    </Stack>
  );
}

ChangeDecision.propTypes = {
  decision: PropTypes.oneOf(["accepted", "rejected"]),
  // Why it can't be rejected, or restored, right now.
  rejectReason: PropTypes.string,
  restoreReason: PropTypes.string,
  // (decision or null): null clears the decision.
  onDecide: PropTypes.func,
};
