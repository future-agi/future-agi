import { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Paper,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import ConfirmDialog from "../../components/ConfirmDialog";
import AdmissionPolicyForm from "./AdmissionPolicyForm";
import AdmissionPolicySummary from "./AdmissionPolicySummary";
import useAdmissionKeys from "./useAdmissionKeys";
import useAdmissionPolicy, { LEDGER_MESSAGE } from "./useAdmissionPolicy";
import {
  CAPS_ON_MESSAGE,
  DEACTIVATE_MESSAGE,
  DEACTIVATE_TITLE,
} from "./capsWording";
import {
  admissionDraft,
  admissionError,
  admissionPayload,
  admissionStatus,
  bindingReport,
  policyRevision,
  readPolicy,
} from "./admissionPolicy";

function ScopedEditor({
  organizationId,
  canEdit,
  blocked,
  onApplied,
  onRead,
  onBusyChange,
}) {
  const policy = useAdmissionPolicy(organizationId, canEdit, onApplied, onRead);
  const [draft, setDraft] = useState(null);
  // The revision a draft kept after a ledger refusal was based on, shown while
  // the read after it failed (no current revision to show).
  const [basis, setBasis] = useState(0);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => {
    onBusyChange?.(Boolean(draft) || policy.saving);
    return () => onBusyChange?.(false);
  }, [draft, policy.saving, onBusyChange]);
  const reload = () => {
    if (policy.saving) return;
    setDraft(null);
    setConfirm(false);
    policy.reload();
  };
  // Keys are read only while a draft is open, and only for Owner/Admin.
  const keys = useAdmissionKeys(organizationId, canEdit && Boolean(draft));
  const report = draft ? bindingReport(draft, policy.config, keys) : null;
  const acknowledged = !report?.needsAck || draft.ack === report.signature;
  let validation = "";
  if (draft) {
    try {
      admissionPayload(policy.config, draft, true);
    } catch (error) {
      validation = error.message;
    }
  }
  // Backend precedence over every stored spelling: a run-only alias policy
  // is configured, and can be deactivated.
  const { configured, enabled } = readPolicy(policy.config);
  // Without the gateway's spend ledger only switching daily caps on is
  // blocked: its control stays shown, disabled, with the reason beside it.
  // Edits to caps already on and deactivation stay available.
  const ledgerMissing = policy.ledger !== "ready";
  const ledgerBlocksOn = ledgerMissing && !enabled;
  const canActivate =
    Boolean(draft) &&
    !validation &&
    !report.loading &&
    acknowledged &&
    !blocked &&
    !ledgerBlocksOn;
  const save = async (activate) => {
    if (!canEdit || blocked || policy.saving) return;
    // The button is disabled in these cases; refuse here too so no stale
    // handler can write a draft that was not validated and acknowledged.
    if (activate && !canActivate) return;
    const submitted = draft;
    const submittedBasis = policyRevision(policy.config);
    setDraft(null);
    setConfirm(false);
    const refused = await policy.save(submitted, activate);
    // Refused for the spend ledger, so nothing was saved: the draft comes
    // back, unless the read after it shows the policy changed meanwhile. A
    // failed read shows no change, so the edits stay; Activate stays off.
    if (
      submitted &&
      refused &&
      (refused.current === undefined ||
        policyRevision(refused.current) === submittedBasis)
    ) {
      setBasis(submittedBasis);
      setDraft(submitted);
    }
  };
  const denied = [401, 403].includes(admissionStatus(policy.error));
  // One notice, beside the action blocked: Activate in an open draft, else
  // the button that opens one.
  const ledgerNoticeId = useId();
  const ledgerNotice = (
    <Alert
      severity="info"
      id={ledgerNoticeId}
      data-testid="admission-ledger-notice"
    >
      {LEDGER_MESSAGE}
    </Alert>
  );
  const noticeBesideActivate = canEdit && Boolean(draft) && ledgerBlocksOn;
  const form = canEdit && draft && (
    <AdmissionPolicyForm
      draft={draft}
      onChange={setDraft}
      busy={blocked || policy.saving}
      revision={policy.error ? basis : policyRevision(policy.config)}
      validation={validation}
      canActivate={canActivate}
      activationNotice={noticeBesideActivate ? ledgerNotice : null}
      report={report}
      keys={keys}
      onReloadKeys={keys.reload}
      onActivate={() => save(true)}
      onCancel={() => setDraft(null)}
    />
  );
  return (
    <Stack spacing={2}>
      {policy.notice && (
        <Alert severity={policy.notice.severity} data-testid="admission-notice">
          {policy.notice.text}
        </Alert>
      )}
      {policy.loading ? (
        <Stack
          data-testid="admission-loading"
          role="status"
          aria-label="Loading admission policy"
        >
          <Skeleton height={80} />
          <Typography variant="body2">
            Loading current admission policy…
          </Typography>
        </Stack>
      ) : policy.error ? (
        <>
          <Alert
            severity="error"
            data-testid={denied ? "admission-denied" : "admission-error"}
          >
            {denied ? "Access denied. " : "Policy could not be loaded. "}
            {admissionError(policy.error)}
          </Alert>
          {/* Only a draft kept after a ledger refusal is open here: its edits
              and the ledger notice beside Activate outlast the failed read. */}
          {!denied && form}
        </>
      ) : (
        <>
          <AdmissionPolicySummary config={policy.config} />
          {ledgerBlocksOn && !noticeBesideActivate && ledgerNotice}
          {enabled && (
            <Alert severity="info" data-testid="admission-caps-on-notice">
              {CAPS_ON_MESSAGE}
            </Alert>
          )}
          {!canEdit && (
            <Alert severity="info">
              Only an Owner or Admin can activate, edit or deactivate admission
              caps. This policy is read-only.
            </Alert>
          )}
          {blocked && canEdit && (
            <Alert severity="warning">
              Save or discard pending legacy budget/routing edits before
              activating admission caps. No unsaved settings are included in
              this activation.
            </Alert>
          )}
          {canEdit && !draft && (
            <Stack direction="row" gap={1} flexWrap="wrap">
              <Button
                data-testid="admission-edit"
                variant="outlined"
                disabled={blocked || policy.saving || ledgerBlocksOn}
                aria-describedby={ledgerBlocksOn ? ledgerNoticeId : undefined}
                onClick={() => setDraft(admissionDraft(policy.config))}
              >
                {configured
                  ? "Edit and activate caps"
                  : "Create activation draft"}
              </Button>
              {configured && (
                <Button
                  data-testid="admission-deactivate"
                  color="error"
                  disabled={blocked || policy.saving}
                  onClick={() => setConfirm(true)}
                >
                  Deactivate admission caps
                </Button>
              )}
            </Stack>
          )}
          {form}
        </>
      )}
      <Button
        size="small"
        sx={{ alignSelf: "flex-start" }}
        onClick={reload}
        disabled={policy.saving || policy.loading}
      >
        Reload admission policy
      </Button>
      <ConfirmDialog
        open={confirm && canEdit}
        onClose={() => setConfirm(false)}
        onConfirm={() => save(false)}
        isLoading={policy.saving || blocked}
        title={DEACTIVATE_TITLE}
        confirmLabel="Deactivate caps"
        message={`${DEACTIVATE_MESSAGE} This also removes the base per-run ceiling for new runs. Existing runs keep their stored limits. Other limits, including routing rollout ceilings, still apply. Spend and first-activation records are not reset. Legacy hierarchical budgets, bindings and other settings remain unchanged. This uses the displayed revision; a stale revision is refused.`}
      />
    </Stack>
  );
}
ScopedEditor.propTypes = {
  organizationId: PropTypes.string.isRequired,
  canEdit: PropTypes.bool.isRequired,
  blocked: PropTypes.bool,
  onApplied: PropTypes.func,
  onRead: PropTypes.func,
  onBusyChange: PropTypes.func,
};

export default function AdmissionCapEditor(props) {
  const { currentOrganizationId } = useOrganization();
  const { canEdit } = useGatewayAdmin();
  return (
    <Paper
      variant="outlined"
      sx={{ p: 2, mb: 3 }}
      data-testid="admission-cap-policy"
    >
      <Typography
        component="h3"
        variant="subtitle1"
        fontWeight="600"
        sx={{ mb: 0.5 }}
      >
        Admission cap activation
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        This is the saved policy. When this gateway may not enforce budgets, a
        note at the top of this tab says so.
      </Typography>
      {currentOrganizationId ? (
        <ScopedEditor
          key={`${currentOrganizationId}:${canEdit}`}
          organizationId={String(currentOrganizationId)}
          canEdit={canEdit}
          {...props}
        />
      ) : (
        <Alert severity="info">
          Select an organization to read its admission policy.
        </Alert>
      )}
    </Paper>
  );
}
