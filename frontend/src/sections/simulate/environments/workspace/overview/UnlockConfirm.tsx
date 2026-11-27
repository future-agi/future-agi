import { Button } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LOCK_COPY, lockPair } from "./overview.constants";

// The lock an unlock ends, as its confirmation names it.
export interface UnlockTarget {
  label: string;
  environmentLabel: string | null;
  suiteLabel: string | null;
}

interface UnlockConfirmProps {
  target: UnlockTarget;
  onClose: () => void;
  onConfirm: () => void;
}

// The one confirmation an unlock waits for, wherever it starts: the locked-version
// card, or the Unlock in a refused delete. Unlocking ends a lock, so it never sends
// on one click.
export default function UnlockConfirm({
  target,
  onClose,
  onConfirm,
}: UnlockConfirmProps) {
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 480 } }}
      title={LOCK_COPY.unlockTitle(
        lockPair(target.environmentLabel, target.suiteLabel),
      )}
      content={LOCK_COPY.unlockBody(target.label)}
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onConfirm}
        >
          {LOCK_COPY.unlockAction}
        </Button>
      }
    />
  );
}
