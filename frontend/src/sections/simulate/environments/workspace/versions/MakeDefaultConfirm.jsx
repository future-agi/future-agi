import PropTypes from "prop-types";
import { useRef } from "react";
import { Button, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { MOVABLE_PIECES } from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import { named } from "../agents/agentVersionStatus";

const COPY = VERSIONS_COPY.makeDefault;

// Asks before a version becomes the default. Stays open while the move is
// pending, so the user sees it land or fail.
export default function MakeDefaultConfirm({
  piece,
  version,
  pending = false,
  rollBack = false,
  onConfirm,
  onClose,
}) {
  // Keep naming the version, and the wording, while the dialog fades out:
  // once the move lands the version is the default, no longer an older one.
  const shown = useRef({ version, back: false });
  if (version) {
    shown.current = { version, back: rollBack && COPY.rollBack.pieces[piece] };
  }
  const label = named(shown.current.version);
  const { back } = shown.current;
  return (
    <ConfirmDialog
      open={Boolean(version)}
      onClose={pending ? undefined : onClose}
      closeDisabled={pending}
      title={
        back
          ? COPY.rollBack.title(back, label)
          : COPY.title(label, COPY.pieces[piece])
      }
      content={
        <Typography component="span" sx={{ typography: "s2" }}>
          {COPY.body}
        </Typography>
      }
      action={
        <Button
          size="small"
          variant="contained"
          onClick={onConfirm}
          disabled={pending}
          sx={{ px: 3 }}
        >
          {back
            ? pending
              ? COPY.rollBack.pending
              : COPY.rollBack.confirm
            : pending
              ? COPY.pending
              : COPY.confirm}
        </Button>
      }
    />
  );
}

MakeDefaultConfirm.propTypes = {
  piece: PropTypes.oneOf(MOVABLE_PIECES).isRequired,
  // The version to make the default; none keeps the dialog closed.
  version: PropTypes.object,
  pending: PropTypes.bool,
  // Worded as going back, for a version older than the default.
  rollBack: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
