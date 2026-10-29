import PropTypes from "prop-types";
import { useSnackbar } from "notistack";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { snapshotRollbackPlan } from "../_mock/rlContract";
import { plural } from "../_mock/nudgeEffects";
import { envRollbackCli } from "../_mock/envCopy";
import CopyCommandButton from "../components/CopyCommandButton";

/**
 * Roll back an environment to an older version — the whole of it: world and
 * contract, scenario set, sub-goals, evaluations and simulated caller. Said
 * before it happens: what changes, what is discarded or kept, and which
 * scenarios re-check. It makes the next version (nothing is deleted), so the
 * History and every run keep saying exactly what they ran on. One dialog for
 * History and Settings; `targetLabel` is the environment version ("v5").
 */
export default function RollbackDialog({
  open,
  targetLabel,
  env,
  envState,
  patch,
  onClose,
}) {
  const { enqueueSnackbar } = useSnackbar();
  if (!open || !targetLabel) return null;
  const plan = snapshotRollbackPlan(env, envState, targetLabel);

  const confirm = () => {
    patch(plan.patch(envState));
    enqueueSnackbar(
      `Environment ${plan.next} — everything as in environment ${plan.snap.label}. Roll forward the same way from History.`,
      { variant: "success" },
    );
    onClose();
  };

  const rows = plan.ok
    ? [
        ...plan.changes,
        ...plan.keeps,
        plan.sitOut > 0 &&
          `${plural(plan.sitOut, "scenario")} need a tool this world won't answer — ${plan.sitOut === 1 ? "it sits" : "they sit"} out`,
        plan.recheck > 0 &&
          `${plural(plan.recheck, "scenario")} proved only on newer versions will be marked Re-check`,
        plan.discards > 0 &&
          `Your ${plural(plan.discards, "unsaved edit")} ${plan.discards === 1 ? "is" : "are"} discarded`,
      ].filter(Boolean)
    : [plan.error];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{
        sx: {
          width: 560,
          maxWidth: "calc(100% - 32px)",
          p: 3,
          borderRadius: 2,
          backgroundImage: "none",
        },
      }}
    >
      <Typography
        sx={{ typography: "m3", fontWeight: 600 }}
      >{`Roll back to environment ${targetLabel}?`}</Typography>
      {plan.ok && (
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {`This makes environment ${plan.next} as it was in ${targetLabel}. Nothing is deleted.`}
        </Typography>
      )}
      <Stack
        spacing={0.5}
        sx={{
          mt: 2,
          px: 1.75,
          py: 1.5,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        {rows.map((line) => (
          <Stack key={line} direction="row" spacing={1} alignItems="flex-start">
            <Box
              sx={{
                width: 4,
                height: 4,
                borderRadius: "50%",
                bgcolor: "text.disabled",
                mt: "8px",
                flexShrink: 0,
              }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {line}
            </Typography>
          </Stack>
        ))}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2.5 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Box flex={1} />
        <CopyCommandButton command={envRollbackCli(env, targetLabel)} />
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={confirm}
          disabled={!plan.ok}
          startIcon={<Iconify icon="solar:history-linear" width={14} />}
        >
          {`Roll back to ${targetLabel}`}
        </Button>
      </Stack>
    </Dialog>
  );
}

RollbackDialog.propTypes = {
  open: PropTypes.bool,
  targetLabel: PropTypes.string,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
