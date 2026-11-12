import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { REASON_MAX, REASON_MIN, REPORT_REASONS } from "../constants";
import { EMAIL_UNVERIFIED, useHubRules, useReport } from "../api/queries";

import EmailVerifyNotice from "./EmailVerifyNotice";

// The private report form (SPEC §6.6): one way, to Future AGI maintainers.
// The reporter sees "Received", then one outcome email. Nothing is public.
export default function ReportDialog({ open, onClose, entryRef, version }) {
  const [reason, setReason] = useState("security");
  const [details, setDetails] = useState("");
  const report = useReport();
  const { data: rules } = useHubRules();
  const length = details.trim().length;
  const valid = length >= REASON_MIN && length <= REASON_MAX;
  const close = () => {
    report.reset();
    setDetails("");
    onClose();
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      fullWidth
      maxWidth="sm"
      data-testid="hub-report-dialog"
    >
      <DialogTitle>Report this entry</DialogTitle>
      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          pt: "8px !important",
        }}
      >
        {report.isSuccess ? (
          <Alert severity="success" data-testid="hub-report-received">
            Received. Future AGI maintainers will look at it, and you will get
            one email with the outcome.
          </Alert>
        ) : (
          <>
            <Typography typography="s2" color="text.secondary">
              {`Your report about ${entryRef}${version ? `@${version}` : ""} goes only to Future AGI maintainers. It is never shown on the Hub.`}
            </Typography>
            <TextField
              select
              label="Reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              size="small"
            >
              {REPORT_REASONS.map((item) => (
                <MenuItem key={item.value} value={item.value}>
                  {item.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="What did you see?"
              value={details}
              onChange={(e) => setDetails(e.target.value)}
              multiline
              minRows={4}
              inputProps={{
                maxLength: REASON_MAX,
                "data-testid": "hub-report-details",
              }}
              helperText={`${length} / ${REASON_MAX} characters, at least ${REASON_MIN}`}
            />
            {rules?.contacts?.security && (
              <Typography typography="s3" color="text.tertiary">
                {`Security problems can also go to ${rules.contacts.security}.`}
              </Typography>
            )}
            {report.isError &&
              (report.error?.code === EMAIL_UNVERIFIED ? (
                <EmailVerifyNotice action="send a report" />
              ) : (
                <Alert severity="error">
                  {report.error?.message || "The report did not send."}
                </Alert>
              ))}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={close} color="inherit">
          {report.isSuccess ? "Close" : "Cancel"}
        </Button>
        {!report.isSuccess && (
          <Button
            variant="contained"
            disabled={!valid || report.isPending}
            onClick={() =>
              report.mutate({
                entry: entryRef,
                version: version || undefined,
                reason,
                details: details.trim(),
              })
            }
            data-testid="hub-report-send"
          >
            Send report
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

ReportDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  entryRef: PropTypes.string.isRequired,
  version: PropTypes.string,
};
