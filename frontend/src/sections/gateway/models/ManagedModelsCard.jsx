import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Divider,
  Skeleton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { GatewayStatusChip } from "../components/frame";
import { SavedButNotAppliedError } from "../providers/hooks/useGatewayConfig";
import { formatBalance } from "../utils/formatters";
import {
  saveErrorMessage,
  useSetManagedModelsEnabled,
} from "./hooks/useManagedModels";

// One customer-facing name for the credits switch and its accessible label.
const CARD_TITLE = "Future AGI credits";

// The switch only turns credits off; turning them on again goes through the
// Future AGI account team, the same team that adds credit.
const TURN_ON_ADMIN = "To turn them on, contact your Future AGI account team.";
const TURN_ON_MEMBER =
  "To turn them on, ask an organization Owner or Admin to contact your Future AGI account team.";

const Stat = ({ label, value, color }) => (
  <Box sx={{ minWidth: 0 }}>
    <Typography typography="s2" color="text.secondary" noWrap component="div">
      {label}
    </Typography>
    <Typography
      typography="s1"
      fontWeight="fontWeightMedium"
      color={color || "text.primary"}
      noWrap
      component="div"
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {value}
    </Typography>
  </Box>
);

Stat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  color: PropTypes.string,
};

// The org's managed-models switch and credit balance, in one calm strip
// above the catalog. Prices are the catalog's (what a request on credits is
// charged); the card states no margin. The balance is cut to the cent (never
// rounded up).
// The console sells no credit: a used-up balance says who adds it.
const ManagedModelsCard = ({
  status,
  isLoading,
  error,
  onRetry,
  gatewayId,
  canWrite,
}) => {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const mutation = useSetManagedModelsEnabled();

  if (isLoading) {
    return <Skeleton variant="rounded" height={64} />;
  }

  if (error || !status) {
    return (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        }
      >
        Couldn&apos;t load your Future AGI credits.
      </Alert>
    );
  }

  // A deployment without Future AGI models has nothing to switch on: the
  // Models pages then open on Auto routing, with no card.
  if (!status.available) return null;
  // The console has no control that turns credits on, so the off state names
  // the real way back instead of a switch.
  if (status.enabled !== true) {
    return (
      <Alert severity="info">
        <Typography typography="s2" component="p">
          Future AGI credits are off for this organization.
        </Typography>
        <Typography typography="s2" component="p">
          {canWrite ? TURN_ON_ADMIN : TURN_ON_MEMBER} Requests through your
          own provider keys keep working.
        </Typography>
      </Alert>
    );
  }

  const balance = Number(status.balance_usd);
  const outOfCredit = Number.isFinite(balance) && balance <= 0;

  const handleConfirm = () => {
    mutation.mutate(
      { gatewayId, enabled: false },
      {
        onSuccess: (result) => {
          setConfirmOpen(false);
          // A change Django saved but the gateway did not apply is reported
          // as that, never with the success toast.
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
            return;
          }
          enqueueSnackbar("Future AGI credits turned off", {
            variant: "success",
          });
        },
        onError: (err) => {
          enqueueSnackbar(
            saveErrorMessage(err, "Failed to update Future AGI credits"),
            { variant: "error" },
          );
        },
      },
    );
  };

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        px: 2,
        py: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={{ xs: 1.5, md: 2 }}
        alignItems={{ xs: "stretch", md: "center" }}
        justifyContent="space-between"
      >
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Stack direction="row" spacing={1} alignItems="center" mb={0.25}>
            <Typography typography="s1" fontWeight="fontWeightMedium">
              {CARD_TITLE}
            </Typography>
            <GatewayStatusChip tone="healthy" label="Enabled" />
          </Stack>
          <Typography typography="s2" color="text.secondary">
            {`Call credit-eligible catalog models with one Future AGI key, without your own provider keys. Requests are billed at the catalog's listed price, drawn from your organization's Future AGI credits. Models marked 'Not available on credits' cannot use this balance.`}
          </Typography>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
          columnGap={2.5}
          rowGap={1}
          divider={<Divider orientation="vertical" flexItem />}
          sx={{ flexShrink: 0 }}
        >
          <Stat
            label="Credit balance"
            value={formatBalance(status.balance_usd)}
            color={outOfCredit ? "error.main" : undefined}
          />
          <GatewayAdminOnly allowed={Boolean(canWrite)}>
            <Switch
              checked
              disabled={!canWrite || mutation.isPending}
              onChange={() => setConfirmOpen(true)}
              inputProps={{ "aria-label": CARD_TITLE }}
            />
          </GatewayAdminOnly>
        </Stack>
      </Stack>

      {outOfCredit && (
        <Alert severity="warning">
          Your Future AGI credits are used up. The gateway refuses requests to
          Future AGI models (HTTP 402) until credit is added; contact your
          Future AGI account team to add credit. Requests through your own
          provider keys are not affected.
        </Alert>
      )}
      {!canWrite && (
        <Typography typography="s2" color="text.secondary" component="p">
          Owner or Admin only: only an organization Owner or Admin can turn
          off Future AGI credits.
        </Typography>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => !mutation.isPending && setConfirmOpen(false)}
        onConfirm={handleConfirm}
        isLoading={mutation.isPending}
        title="Turn off Future AGI credits?"
        message="Requests to Future AGI models will be refused. Requests through your own provider keys keep working. You cannot turn Future AGI credits back on from the console; to turn them on again, contact your Future AGI account team."
        confirmLabel="Turn off"
        confirmColor="error"
      />
    </Box>
  );
};

ManagedModelsCard.propTypes = {
  status: PropTypes.shape({
    available: PropTypes.bool,
    enabled: PropTypes.bool,
    balance_usd: PropTypes.number,
  }),
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  onRetry: PropTypes.func,
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
};

export default ManagedModelsCard;
