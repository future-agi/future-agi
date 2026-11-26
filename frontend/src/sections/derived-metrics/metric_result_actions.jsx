import PropTypes from "prop-types";
import {
  Alert,
  DialogContent,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import TextField from "./MetricField";

export default function MetricResultActions({
  action,
  onClose,
  busy,
  readOnly,
  dirty,
  matched,
  pending,
  dashboardName,
  monitorName,
  threshold,
  operator,
  alertWhen,
  email,
  onChange,
  onDashboard,
  onMonitor,
  feedback,
  monitorLocked,
  dashboardBlocked,
  error,
}) {
  const dashboard = action === "dashboard";
  return (
    <ModalWrapper
      open={Boolean(action)}
      onClose={onClose}
      isLoading={busy}
      title={dashboard ? "Create dashboard from this run" : "Create monitor"}
      actionBtnTitle={dashboard ? "Create dashboard" : "Create monitor"}
      cancelBtnTitle="Close"
      closeButtonLabel="Close dialog"
      actionBtnProps={{ color: "inherit" }}
      modalWidth="600px"
      isValid={
        !readOnly &&
        (dashboard
          ? Boolean(dashboardName.trim()) &&
            matched &&
            !pending &&
            !dashboardBlocked
          : !dirty &&
            Boolean(monitorName.trim()) &&
            threshold.trim() !== "" &&
            Number.isFinite(Number(threshold)) &&
            !monitorLocked)
      }
      onSubmit={dashboard ? onDashboard : onMonitor}
    >
      <DialogContent>
        <Stack gap={2}>
          {error && <Alert severity="error">{error}</Alert>}
          {dashboard ? (
            <>
              <Typography variant="body2">
                The new dashboard pins this run and its exact window. It never
                averages daily ratios.
              </Typography>
              <TextField
                autoFocus
                fullWidth
                label="Dashboard name"
                value={dashboardName}
                onChange={(e) => onChange("dashboardName", e.target.value)}
              />
              {(!matched || pending) && (
                <Alert severity="warning">
                  Wait for a verified committed result before creating a
                  dashboard.
                </Alert>
              )}
            </>
          ) : (
            <>
              <Typography variant="body2">
                Monitors consume new completed runs for the current version,
                using their frozen filters. They do not schedule computation. No
                value means no alert, not a healthy zero.
              </Typography>
              <TextField
                autoFocus
                fullWidth
                label="Monitor name"
                value={monitorName}
                onChange={(e) => onChange("monitorName", e.target.value)}
              />
              <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
                <TextField
                  select
                  fullWidth
                  label="Alert when"
                  value={operator}
                  onChange={(e) => onChange("operator", e.target.value)}
                >
                  <MenuItem value="less_than">Below</MenuItem>
                  <MenuItem value="greater_than">Above</MenuItem>
                </TextField>
                <TextField
                  fullWidth
                  label="Threshold"
                  type="number"
                  value={threshold}
                  onChange={(e) => onChange("threshold", e.target.value)}
                />
              </Stack>
              <TextField
                select
                label="Alert evidence"
                value={alertWhen}
                onChange={(e) => onChange("alertWhen", e.target.value)}
                helperText="Whole-interval mode requires the entire fixed-window 95% interval to breach. Missing intervals do not alert."
              >
                <MenuItem value="point">Point estimate (default)</MenuItem>
                <MenuItem value="confident">Whole 95% interval</MenuItem>
              </TextField>
              <Typography variant="caption" color="text.secondary">
                Repeated or overlapping windows are not sequentially corrected.
              </Typography>
              <TextField
                label="Notification email (optional)"
                type="email"
                value={email}
                onChange={(e) => onChange("email", e.target.value)}
              />
              {feedback && (
                <Alert
                  aria-label="Monitor feedback"
                  severity={feedback.severity}
                >
                  {feedback.message}
                </Alert>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
    </ModalWrapper>
  );
}
MetricResultActions.propTypes = {
  action: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  readOnly: PropTypes.bool,
  dirty: PropTypes.bool,
  matched: PropTypes.bool,
  pending: PropTypes.bool,
  dashboardName: PropTypes.string,
  monitorName: PropTypes.string,
  threshold: PropTypes.string,
  operator: PropTypes.string,
  alertWhen: PropTypes.string,
  email: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onDashboard: PropTypes.func.isRequired,
  onMonitor: PropTypes.func.isRequired,
  feedback: PropTypes.object,
  monitorLocked: PropTypes.bool,
  dashboardBlocked: PropTypes.bool,
  error: PropTypes.string,
};
