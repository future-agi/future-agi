import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Skeleton,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { SavedButNotAppliedError } from "../providers/hooks/useGatewayConfig";
import { formatPercent } from "./catalog";
import {
  saveErrorMessage,
  useSetManagedModelsEnabled,
} from "./hooks/useManagedModels";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

function formatBalance(value) {
  const n = Number(value);
  return value == null || !Number.isFinite(n) ? "—" : usd.format(n);
}

const Stat = ({ label, value, color }) => (
  <Box sx={{ minWidth: 0 }}>
    <Typography variant="caption" color="text.secondary" noWrap>
      {label}
    </Typography>
    <Typography variant="subtitle2" color={color} noWrap>
      {value}
    </Typography>
  </Box>
);

Stat.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  color: PropTypes.string,
};

function statusChip(status) {
  return status.enabled
    ? { label: "Enabled", color: "success" }
    : { label: "Off", color: "default" };
}

const ManagedModelsCard = ({
  status,
  isLoading,
  error,
  onRetry,
  gatewayId,
  canWrite,
}) => {
  const [pending, setPending] = useState(null); // true = enabling
  const mutation = useSetManagedModelsEnabled();

  if (isLoading) {
    return <Skeleton variant="rounded" height={96} sx={{ mb: 3 }} />;
  }

  if (error || !status) {
    return (
      <Alert
        severity="error"
        sx={{ mb: 3 }}
        action={
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        }
      >
        Couldn&apos;t load managed model status.
      </Alert>
    );
  }

  // A deployment without managed models has nothing to switch on: the
  // Models page then shows Auto routing alone, with no card.
  if (!status.available) return null;

  const markup = formatPercent(status.markup_percent);
  const balance = Number(status.balance_usd);
  const outOfCredit =
    status.enabled && Number.isFinite(balance) ? balance <= 0 : false;
  const chip = statusChip(status);

  const handleConfirm = () => {
    const enabled = pending;
    mutation.mutate(
      { gatewayId, enabled },
      {
        onSuccess: (result) => {
          setPending(null);
          // A change Django saved but the gateway did not apply is
          // reported as that, never with the success toast.
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
            return;
          }
          enqueueSnackbar(
            enabled ? "Managed models enabled" : "Managed models disabled",
            { variant: "success" },
          );
        },
        onError: (err) => {
          enqueueSnackbar(
            saveErrorMessage(err, "Failed to update managed models"),
            { variant: "error" },
          );
        },
      },
    );
  };

  return (
    <Card variant="outlined" sx={{ mb: 3 }}>
      <CardContent sx={{ py: 2, "&:last-child": { pb: 2 } }}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={2}
          alignItems={{ xs: "stretch", md: "center" }}
          justifyContent="space-between"
        >
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="flex-start"
            sx={{ minWidth: 0, flex: 1 }}
          >
            <Iconify
              icon="mdi:cloud-check-outline"
              width={22}
              sx={{ color: "primary.main", mt: 0.25, flexShrink: 0 }}
            />
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} alignItems="center" mb={0.25}>
                <Typography variant="subtitle1">Managed models</Typography>
                <Chip
                  size="small"
                  variant="outlined"
                  label={chip.label}
                  color={chip.color}
                />
              </Stack>
              <Typography variant="body2" color="text.secondary">
                {`Call catalog models through FutureAGI's provider accounts, without adding your own keys. Usage is billed at provider cost + ${markup} from your organization's managed-model credit.`}
              </Typography>
            </Box>
          </Stack>

          <Stack
            direction="row"
            spacing={3}
            alignItems="center"
            divider={<Divider orientation="vertical" flexItem />}
            sx={{ flexShrink: 0, pl: { xs: 4.5, md: 0 } }}
          >
            <Stat label="Markup" value={markup} />
            <Stat
              label="Credit balance"
              value={formatBalance(status.balance_usd)}
              color={outOfCredit ? "error.main" : undefined}
            />
            <GatewayAdminOnly allowed={Boolean(canWrite)}>
              <Switch
                checked={Boolean(status.enabled)}
                disabled={!canWrite || mutation.isPending}
                onChange={(e) => setPending(e.target.checked)}
                inputProps={{ "aria-label": "Use managed models" }}
              />
            </GatewayAdminOnly>
          </Stack>
        </Stack>

        {outOfCredit && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            Your managed-model credit is used up. The gateway refuses requests
            to managed models (HTTP 402) until credit is added; contact your
            FutureAGI account team to add credit. Requests through your own
            provider keys are not affected.
          </Alert>
        )}
        {!canWrite && (
          <Typography
            variant="caption"
            color="text.secondary"
            component="p"
            sx={{ mt: 1.5 }}
          >
            Owner or Admin only: only an organization Owner or Admin can turn
            managed models on or off.
          </Typography>
        )}
      </CardContent>

      <ConfirmDialog
        open={pending !== null}
        onClose={() => !mutation.isPending && setPending(null)}
        onConfirm={handleConfirm}
        isLoading={mutation.isPending}
        title={pending ? "Enable managed models?" : "Disable managed models?"}
        message={
          pending
            ? `Requests to managed models are billed to your organization at provider cost + ${markup}, drawn from its managed-model credit. Requests through your own provider keys are not affected.`
            : "Requests to managed models will be refused. Requests through your own provider keys keep working."
        }
        confirmLabel={pending ? "Enable" : "Disable"}
        confirmColor={pending ? "primary" : "error"}
      />
    </Card>
  );
};

ManagedModelsCard.propTypes = {
  status: PropTypes.shape({
    available: PropTypes.bool,
    enabled: PropTypes.bool,
    balance_usd: PropTypes.number,
    markup_percent: PropTypes.number,
  }),
  isLoading: PropTypes.bool,
  error: PropTypes.object,
  onRetry: PropTypes.func,
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
};

export default ManagedModelsCard;
