/**
 * Payment Methods — list, add, set-default, delete cards via Stripe.
 *
 * Card collection uses Stripe Checkout in `setup` mode: POST returns a
 * checkout_url; we redirect the browser to it. Stripe attaches the card
 * to the customer automatically and redirects back to ?card=added, where
 * a mount-time effect refetches the card list and shows a toast.
 */

import { useState, useCallback, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Button,
  Chip,
  IconButton,
  Skeleton,
  Alert,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const BRAND_ICONS = {
  visa: "logos:visa",
  mastercard: "logos:mastercard",
  amex: "fontisto:american-express",
};

export default function PaymentMethods() {
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [cardError, setCardError] = useState("");
  const [cardLoading, setCardLoading] = useState(false);
  const queryClient = useQueryClient();

  const { data: methods, isLoading } = useQuery({
    queryKey: ["v2-payment-methods"],
    queryFn: () => axios.get(endpoints.settings.v2.paymentMethods),
    select: (res) => (Array.isArray(res.data?.result) ? res.data.result : []),
  });

  const setDefaultMutation = useMutation({
    mutationFn: (pmId) =>
      axios.post(endpoints.settings.v2.paymentMethodDefault(pmId)),
    meta: { errorHandled: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-payment-methods"] });
      enqueueSnackbar("Default payment method updated", { variant: "success" });
    },
    onError: (err) =>
      enqueueSnackbar(err?.result || "Failed to set default", {
        variant: "error",
      }),
  });

  const deleteMutation = useMutation({
    mutationFn: (pmId) =>
      axios.delete(endpoints.settings.v2.paymentMethodDelete(pmId)),
    meta: { errorHandled: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-payment-methods"] });
      setDeleteTarget(null);
      enqueueSnackbar("Payment method removed", { variant: "success" });
    },
    onError: (err) => {
      setDeleteTarget(null);
      enqueueSnackbar(err?.result || "Cannot remove this card", {
        variant: "error",
      });
    },
  });

  const handleAddCard = useCallback(async () => {
    setCardError("");
    setCardLoading(true);
    try {
      const res = await axios.post(
        endpoints.settings.v2.paymentMethodSetupIntent,
      );
      const checkoutUrl = res.data?.result?.checkout_url;
      if (!checkoutUrl) throw new Error("No checkout URL returned");
      window.location.href = checkoutUrl;
    } catch (err) {
      setCardError(err?.message || "Failed to start card setup");
      setCardLoading(false);
    }
  }, []);

  // Handle return from Stripe Checkout (setup mode). On success, call the
  // backend PUT to confirm the session — it auto-defaults the card if the
  // customer has no other default, matching the PAYG-upgrade flow.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const cardStatus = params.get("card");
    if (!cardStatus) return;

    const sessionId = params.get("session_id");

    // Strip our query params before any async work so a slow confirm
    // request can't double-fire if the effect re-runs.
    params.delete("card");
    params.delete("session_id");
    const qs = params.toString();
    const newUrl = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
    window.history.replaceState({}, "", newUrl);

    if (cardStatus === "added") {
      const finalize = () => {
        queryClient.invalidateQueries({ queryKey: ["v2-payment-methods"] });
        enqueueSnackbar("Card added successfully", { variant: "success" });
      };
      if (sessionId) {
        axios
          .put(endpoints.settings.v2.paymentMethods, { session_id: sessionId })
          .finally(finalize);
      } else {
        finalize();
      }
    } else if (cardStatus === "cancelled") {
      enqueueSnackbar("Card setup cancelled", { variant: "info" });
    }
  }, [queryClient]);

  if (isLoading) return <Skeleton variant="rounded" height={100} />;

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={2}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          Payment Methods
        </Typography>
        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:add-linear" />}
          onClick={() => setAddDialogOpen(true)}
        >
          Add Card
        </Button>
      </Stack>

      {!methods || methods.length === 0 ? (
        <Section variant="panel" sx={{ p: 3, textAlign: "center" }}>
          <Iconify
            icon="solar:card-linear"
            width={36}
            sx={{ color: "text.disabled", mb: 1 }}
          />
          <Typography variant="body2" color="text.secondary">
            No payment methods on file. Add a card to enable paid features.
          </Typography>
        </Section>
      ) : (
        <Stack spacing={0}>
          {methods.map((pm) => {
            const brandIcon =
              BRAND_ICONS[pm.brand?.toLowerCase()] || "solar:card-linear";
            return (
              <Section
                key={pm.id}
                sx={{ px: 0, py: 1, borderBottom: "1px solid" }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Iconify icon={brandIcon} width={28} />
                    <Box>
                      <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
                        {pm.brand} •••• {pm.last4}
                      </Typography>
                      <Typography variant="caption" color="text.tertiary">
                        Expires {pm.exp_month}/{pm.exp_year}
                      </Typography>
                    </Box>
                    {pm.is_default && (
                      <Chip label="Default" size="small" color="primary" />
                    )}
                  </Stack>
                  <Stack direction="row" spacing={0.5}>
                    {!pm.is_default && (
                      <Button
                        size="small"
                        variant="text"
                        onClick={() => setDefaultMutation.mutate(pm.id)}
                        disabled={setDefaultMutation.isPending}
                      >
                        Set default
                      </Button>
                    )}
                    <IconButton
                      size="small"
                      onClick={() => setDeleteTarget(pm)}
                      title="Remove card"
                    >
                      <Iconify icon="solar:trash-bin-trash-linear" width={20} />
                    </IconButton>
                  </Stack>
                </Stack>
              </Section>
            );
          })}
        </Stack>
      )}

      {/* Add Card Dialog */}
      <CustomDialog
        open={addDialogOpen}
        onClose={() => {
          setAddDialogOpen(false);
          setCardError("");
        }}
        title="Add Payment Method"
        actionButton="Add Card"
        onClickAction={handleAddCard}
        loading={cardLoading}
        preTitleIcon="solar:card-linear"
      >
        <Box sx={{ px: 2, py: 1 }}>
          <Typography variant="body2" color="text.secondary" mb={2}>
            You&apos;ll be redirected to Stripe to securely enter your card
            details.
          </Typography>
          {cardError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {cardError}
            </Alert>
          )}
        </Box>
      </CustomDialog>

      {/* Delete Confirmation Dialog */}
      <CustomDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Remove Payment Method?"
        actionButton="Remove"
        color="error"
        onClickAction={() => deleteMutation.mutate(deleteTarget?.id)}
        loading={deleteMutation.isPending}
        preTitleIcon="solar:danger-circle-linear"
      >
        <Box sx={{ px: 2, py: 1 }}>
          <Typography variant="body2" color="text.secondary">
            Remove {deleteTarget?.brand} •••• {deleteTarget?.last4}? This cannot
            be undone.
          </Typography>
        </Box>
      </CustomDialog>
    </Box>
  );
}
