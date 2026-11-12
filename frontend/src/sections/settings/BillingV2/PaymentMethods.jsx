/**
 * Payment Methods — list, add, set-default, delete cards via Stripe.
 *
 * Card collection uses Stripe Checkout in `setup` mode: POST returns a
 * checkout_url; we redirect the browser to it. Stripe attaches the card
 * to the customer automatically and redirects back to ?card=added, where
 * a mount-time effect confirms the session before reporting success.
 */

import EmptyState from "src/components/empty-state";
import React, { useState, useCallback, useEffect, useRef, useId } from "react";
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
import { useGatewayAdmin } from "src/sections/gateway/hooks/useGatewayAdmin";
import Iconify from "src/components/iconify";
import LoadFailedState from "src/components/load-failed-state";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const BRAND_ICONS = {
  visa: "logos:visa",
  mastercard: "logos:mastercard",
  amex: "fontisto:american-express",
};

// Stripe sends the brand as a lowercase code ("visa"); show its name.
const BRAND_NAMES = {
  visa: "Visa",
  mastercard: "Mastercard",
  amex: "American Express",
  discover: "Discover",
  diners: "Diners Club",
  jcb: "JCB",
  unionpay: "UnionPay",
};

function brandName(brand) {
  if (!brand) return "Card";
  const code = String(brand).toLowerCase();
  return BRAND_NAMES[code] || code.charAt(0).toUpperCase() + code.slice(1);
}

// Every member sees the card controls; changing a payment method needs an
// organization Owner or Admin, so below that they show disabled with this.
const CARD_EDIT_REASON =
  "Only an organization Owner or Admin can change payment methods.";

export default function PaymentMethods() {
  const { canEdit } = useGatewayAdmin();
  const reasonId = useId();
  const describedBy = canEdit ? undefined : reasonId;
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  // The Remove dialog keeps naming its card while it fades out; the card is
  // let go only once the dialog has gone (see its onExited).
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cardError, setCardError] = useState("");
  const [cardLoading, setCardLoading] = useState(false);
  const [confirmationError, setConfirmationError] = useState("");
  const [confirmationLoading, setConfirmationLoading] = useState(false);
  const confirmationSession = useRef(null);
  const returnHandled = useRef(false);
  const queryClient = useQueryClient();

  const {
    data: methods,
    isLoading,
    isError,
    refetch,
    isRefetching,
  } = useQuery({
    queryKey: ["v2-payment-methods"],
    queryFn: () => axios.get(endpoints.settings.v2.paymentMethods),
    select: (res) => (Array.isArray(res.data?.result) ? res.data.result : []),
  });

  const setDefaultMutation = useMutation({
    mutationFn: (pmId) => {
      if (!canEdit) throw new Error("Organization Owner or Admin required");
      return axios.post(endpoints.settings.v2.paymentMethodDefault(pmId));
    },
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
    mutationFn: (pmId) => {
      if (!canEdit) throw new Error("Organization Owner or Admin required");
      return axios.delete(endpoints.settings.v2.paymentMethodDelete(pmId));
    },
    meta: { errorHandled: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["v2-payment-methods"] });
      setDeleteOpen(false);
      enqueueSnackbar("Payment method removed", { variant: "success" });
    },
    onError: (err) => {
      setDeleteOpen(false);
      enqueueSnackbar(err?.result || "Cannot remove this card", {
        variant: "error",
      });
    },
  });

  const handleAddCard = useCallback(async () => {
    if (!canEdit) return;
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
  }, [canEdit]);

  const clearReturnParams = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    params.delete("card");
    params.delete("session_id");
    const qs = params.toString();
    const newUrl = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", newUrl);
  }, []);

  const confirmCard = useCallback(async () => {
    if (!canEdit) return;
    setConfirmationError("");
    setConfirmationLoading(true);
    try {
      const res = await axios.put(endpoints.settings.v2.paymentMethods, {
        session_id: confirmationSession.current,
      });
      if (res.data?.status !== true || !res.data?.result?.payment_method_id) {
        throw new Error("Card confirmation was not accepted");
      }
      clearReturnParams();
      queryClient.invalidateQueries({ queryKey: ["v2-payment-methods"] });
      enqueueSnackbar("Card added successfully", { variant: "success" });
    } catch {
      setConfirmationError(
        "We couldn't confirm your card. Retry confirmation before using it for billing.",
      );
    } finally {
      setConfirmationLoading(false);
    }
  }, [canEdit, clearReturnParams, queryClient]);

  // Keep return parameters until confirmation succeeds so reload can retry.
  // The ref prevents duplicate requests if the mount effect runs again.
  useEffect(() => {
    // A checkout return is a write too. Wait for selected-org authority;
    // workspace_admin and a home-org role do not grant billing access.
    if (!canEdit) return;
    const params = new URLSearchParams(window.location.search);
    const cardStatus = params.get("card");
    if (!cardStatus || returnHandled.current) return;
    returnHandled.current = true;

    if (cardStatus === "added") {
      confirmationSession.current = params.get("session_id");
      if (confirmationSession.current) {
        confirmCard();
      } else {
        setConfirmationError(
          "Missing card setup session. Please add your card again.",
        );
      }
    } else if (cardStatus === "cancelled") {
      clearReturnParams();
      enqueueSnackbar("Card setup cancelled", { variant: "info" });
    }
  }, [canEdit, clearReturnParams, confirmCard]);

  if (isLoading) return <Skeleton variant="rounded" height={100} />;

  if (isError && !methods) {
    return (
      <LoadFailedState
        title="Payment methods couldn't be loaded"
        onRetry={refetch}
        retrying={isRefetching}
      />
    );
  }

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={2}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          Payment methods
        </Typography>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          {!canEdit && (
            <Typography id={reasonId} variant="caption" color="text.secondary">
              {CARD_EDIT_REASON}
            </Typography>
          )}
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:add-linear" />}
            onClick={() => setAddDialogOpen(true)}
            disabled={!canEdit}
            aria-describedby={describedBy}
            sx={{ flexShrink: 0 }}
          >
            Add Card
          </Button>
        </Stack>
      </Stack>

      {(confirmationError || confirmationLoading) && (
        <Alert
          severity={confirmationError ? "error" : "info"}
          sx={{ mb: 2 }}
          action={
            canEdit && confirmationError && confirmationSession.current ? (
              <Button size="small" onClick={confirmCard}>
                Retry confirmation
              </Button>
            ) : undefined
          }
        >
          {confirmationError || "Confirming your card…"}
        </Alert>
      )}

      {!methods || methods.length === 0 ? (
        <Section variant="panel" sx={{ p: 0 }}>
          <EmptyState
            icon="solar:card-linear"
            title="No payment methods yet"
            description="No payment methods on file. Add a card to enable paid features."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => setAddDialogOpen(true)}
                disabled={!canEdit}
                aria-describedby={describedBy}
              >
                Add card
              </Button>
            }
            sx={{ pt: 6, pb: 6 }}
          />
        </Section>
      ) : (
        <Stack spacing={1.5}>
          {methods.map((pm) => {
            const brandIcon =
              BRAND_ICONS[pm.brand?.toLowerCase()] || "solar:card-linear";
            // Multicolour brand logos are drawn for a light ground; single-
            // colour icons draw in the text colour and need no plate.
            const onPlate = brandIcon.startsWith("logos:");
            return (
              <Section
                variant="panel"
                key={pm.id}
                data-testid="payment-method-row"
                sx={{ p: 2 }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    {onPlate ? (
                      <Box
                        data-testid="card-brand-plate"
                        sx={{
                          display: "inline-flex",
                          px: 0.5,
                          py: 0.25,
                          borderRadius: 0.5,
                          bgcolor: "common.white",
                        }}
                      >
                        <Iconify icon={brandIcon} width={28} />
                      </Box>
                    ) : (
                      <Iconify icon={brandIcon} width={28} />
                    )}
                    <Box>
                      <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
                        {brandName(pm.brand)} •••• {pm.last4}
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
                        disabled={!canEdit || setDefaultMutation.isPending}
                        aria-describedby={describedBy}
                      >
                        Set default
                      </Button>
                    )}
                    <IconButton
                      size="small"
                      onClick={() => {
                        setDeleteTarget(pm);
                        setDeleteOpen(true);
                      }}
                      title="Remove card"
                      disabled={!canEdit}
                      aria-describedby={describedBy}
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
      <ConfirmDialog
        open={canEdit && addDialogOpen}
        onClose={() => {
          // The redirect to Stripe is under way; stay until it leaves.
          if (cardLoading) return;
          setAddDialogOpen(false);
          setCardError("");
        }}
        closeDisabled={cardLoading}
        title="Add Payment Method"
        content={
          <>
            You&apos;ll be redirected to Stripe to securely enter your card
            details.
            {cardError && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {cardError}
              </Alert>
            )}
          </>
        }
        action={
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={cardLoading}
            onClick={handleAddCard}
          >
            Add Card
          </LoadingButton>
        }
      />

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        open={canEdit && deleteOpen}
        onClose={() => {
          if (deleteMutation.isPending) return;
          setDeleteOpen(false);
        }}
        // Once it has gone, however it closed (a role change included), the
        // dialog forgets its card and stays shut.
        TransitionProps={{
          onExited: () => {
            setDeleteTarget(null);
            setDeleteOpen(false);
          },
        }}
        closeDisabled={deleteMutation.isPending}
        title="Remove Payment Method?"
        content={`Remove ${brandName(deleteTarget?.brand)} •••• ${deleteTarget?.last4 ?? ""}? This cannot be undone.`}
        action={
          <LoadingButton
            size="small"
            variant="contained"
            color="error"
            loading={deleteMutation.isPending}
            onClick={() => {
              if (canEdit && deleteOpen && deleteTarget)
                deleteMutation.mutate(deleteTarget.id);
            }}
          >
            Remove
          </LoadingButton>
        }
      />
    </Box>
  );
}
