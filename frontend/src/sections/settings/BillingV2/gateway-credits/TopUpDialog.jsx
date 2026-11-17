import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { useStartCheckout } from "./api";
import { formatAmount } from "./format";
import { checkoutUrls } from "./links";

// Buy AI Gateway credit through the existing checkout endpoint
// (managed-credits/checkout/), then continue on the payment page.
export default function TopUpDialog({ open, onClose, minTopup }) {
  const titleId = useId();
  const [amount, setAmount] = useState(String(minTopup ?? ""));
  const [error, setError] = useState("");
  const checkout = useStartCheckout();

  const submit = (event) => {
    event.preventDefault();
    const value = Number(amount);
    if (!Number.isFinite(value) || value < Number(minTopup)) {
      setError(`The minimum top-up is ${formatAmount(minTopup)}.`);
      return;
    }
    setError("");
    checkout.mutate(
      {
        // A number, as the checkout contract publishes it (BILLING-CONTRACT-1).
        amount_usd: Math.round(value * 100) / 100,
        ...checkoutUrls(window.location.origin),
      },
      {
        onSuccess: (res) => {
          const url = res.data?.result?.checkout_url;
          if (url) window.location.assign(url);
          else setError("The payment page could not be opened. Try again.");
        },
        onError: (err) =>
          setError(
            typeof err?.result === "string"
              ? err.result
              : err?.result?.message ||
                  (typeof err?.error === "string" ? err.error : "") ||
                  "Credit purchase is not available right now. Nothing was charged.",
          ),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby={titleId}
      fullWidth
      maxWidth="xs"
      PaperProps={{ component: "form", onSubmit: submit, noValidate: true }}
    >
      <DialogTitle id={titleId}>Add credits</DialogTitle>
      <DialogContent>
        <Typography typography="s1" color="text.secondary" sx={{ mb: 2 }}>
          Future AGI credits pay for credit-eligible catalog models through the
          AI Gateway. Select Continue to payment to request a checkout page.
        </Typography>
        <TextField
          autoFocus
          fullWidth
          size="small"
          type="number"
          label="Amount (USD)"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          inputProps={{ min: minTopup, step: "0.01" }}
          error={!!error}
          helperText={error || `At least ${formatAmount(minTopup)}.`}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          type="submit"
          variant="contained"
          color="primary"
          disabled={checkout.isPending}
        >
          Continue to payment
        </Button>
      </DialogActions>
    </Dialog>
  );
}

TopUpDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  minTopup: PropTypes.number,
};
