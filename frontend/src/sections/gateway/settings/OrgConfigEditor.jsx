/* eslint-disable react/prop-types */
import React, { useState, useEffect, useRef } from "react";
import { isEqual } from "lodash";
import {
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Tab,
  Box,
  Stack,
  Typography,
  CircularProgress,
} from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";
import { useOrganization } from "src/contexts/OrganizationContext";
import AdmissionCapEditor from "../budgets/admission/AdmissionCapEditor";
import { normalizeBudgets } from "../budgets/admission/admissionAliases";

import {
  GUARDRAILS_OFF_BANNER,
  guardrailsAreOff,
} from "../utils/guardrailPipeline";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import {
  buildConfigPatch,
  guardrailsForSave,
  useConfigPatch,
} from "../hooks/useConfigPatch";
import {
  useGatewayConfig,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { useOrgConfig } from "../providers/hooks/useOrgConfig";
import { isOrgConfigTabHidden } from "../shell/page-status";
import { editorTabId, editorTabsFor } from "./orgConfigTabs";
import ProviderConfigTab from "./ProviderConfigTab";
import GuardrailConfigTab from "./GuardrailConfigTab";
import RoutingConfigTab from "./RoutingConfigTab";
import CacheConfigTab from "./CacheConfigTab";
import RateLimitingConfigTab from "./RateLimitingConfigTab";
import BudgetsConfigTab from "./BudgetsConfigTab";
import SecurityConfigTab from "./SecurityConfigTab";
import AlertingConfigTab from "./AlertingConfigTab";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";
import ModelMapConfigTab from "./ModelMapConfigTab";
import ReadOnlyOrgConfigTab from "./ReadOnlyOrgConfigTab";
import { notEnforcedReason, sectionEnforcement } from "./sectionEnforcement";
import SectionEnforcementNotes from "./ui/SectionEnforcementNotes";
import { normalizeFallbackRouting } from "../fallbacks/normalizeFallbackRouting";

// The sections the editor edits, each with the camelCase name an older payload
// may carry. Providers are not among them: they are managed on the Providers
// page, and update-config does not accept them. Audit and A2A have
// read-only tabs: neither belongs in the draft or save allowlist, so a save never sends,
// rewrites or drops a stored section. MCP is managed on its own page.
const SECTIONS = [
  ["guardrails"],
  ["routing"],
  ["cache"],
  ["rate_limiting", "rateLimiting"],
  ["budgets"],
  ["cost_tracking", "costTracking"],
  ["ip_acl", "ipAcl"],
  ["alerting"],
  ["privacy"],
  ["tool_policy", "toolPolicy"],
  ["model_database", "modelDatabase"],
  ["model_map", "modelMap"],
].filter(([key]) => !isOrgConfigTabHidden(key));
const SECTION_KEYS = SECTIONS.map(([key]) => key);

function readSections(config) {
  const sections = Object.fromEntries(
    SECTIONS.map(([key, alias]) => [
      key,
      config?.[key] || (alias && config?.[alias]) || {},
    ]),
  );
  return { ...sections, routing: normalizeFallbackRouting(sections.routing) };
}

// Reload pushes the stored config; it must never create another version. Keep
// its outcome inline even when the shared hook's warning toast has expired.
function RetryConfigApply({ receipt, disabled, onApplied, onBusyChange }) {
  const reload = useReloadConfig();
  const { refetch } = useOrgConfig();
  const [checking, setChecking] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const active = useRef(false);
  const inFlight = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      onBusyChange(false);
    };
  }, [onBusyChange]);

  const matchesReceipt = (read) =>
    !read.isError &&
    receipt.version != null &&
    read.data?.version === receipt.version &&
    (read.data.organization == null ||
      String(read.data.organization) === String(receipt.organizationId));
  const retry = async () => {
    if (disabled || inFlight.current || reload.isPending) return;
    inFlight.current = true;
    onBusyChange(true);
    setChecking(true);
    setUnconfirmed(false);
    try {
      // Reload applies the active version, not the version on our receipt.
      // A retained pre-save read is enough to keep a draft, not to choose
      // which version to push. Check again after reload, whose response
      // currently confirms sync but does not identify a version.
      const before = await refetch();
      if (!active.current) return;
      if (!matchesReceipt(before)) {
        setUnconfirmed(true);
        return;
      }
      const result = await reload.mutateAsync(receipt.gatewayId);
      if (!active.current) return;
      const after = await refetch();
      if (!active.current) return;
      if (
        result?.gateway_synced === true &&
        (result.version == null || result.version === receipt.version) &&
        matchesReceipt(after)
      ) {
        onApplied({ ...receipt, applyState: "applied" });
      } else {
        setUnconfirmed(true);
      }
    } catch {
      // Includes stored-but-not-applied responses rejected by the shared hook.
      if (active.current) setUnconfirmed(true);
    } finally {
      inFlight.current = false;
      if (active.current) {
        setChecking(false);
        onBusyChange(false);
      }
    }
  };

  return (
    <Stack spacing={1} alignItems="flex-start">
      <Button
        size="small"
        variant="outlined"
        onClick={retry}
        disabled={disabled || checking || reload.isPending}
      >
        {checking || reload.isPending ? "Applying…" : "Retry apply"}
      </Button>
      {unconfirmed && (
        <Typography variant="body2">
          Retry did not confirm application. The saved configuration is still
          stored.
        </Typography>
      )}
    </Stack>
  );
}

const ScopedOrgConfigEditor = ({
  open,
  onClose,
  initialConfig,
  initialConfigUpdatedAt,
  defaultTab = 0,
  onSaved,
  savedReceipt = null,
  gatewayId,
  managedOffered,
  currentOrganizationId,
}) => {
  // Anyone may open the editor to read the config; saving it is Owner or
  // Admin only (useGatewayAdmin).
  const { canEdit } = useGatewayAdmin();
  const [admissionBusy, setAdmissionBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [receipt, setReceipt] = useState(savedReceipt);
  const [savedVersion, setSavedVersion] = useState(null);
  const [saveUnconfirmed, setSaveUnconfirmed] = useState(false);
  const savedSnapshot = useRef(null);
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const saveConfig = useConfigPatch({
    action: "Saving org config",
    successMessage: "Org config saved",
  });
  const [tab, setTab] = useState(() => editorTabId(defaultTab));
  const wasOpenRef = useRef(false);
  // What the draft was opened from: the save sends only what differs from it.
  const [saved, setSaved] = useState(() => readSections(null));
  const [draft, setDraft] = useState(() => readSections(null));
  const latestDraft = useRef(draft);
  latestDraft.current = draft;
  // The gateway's own report of what it enforces and how it runs guardrails.
  const gatewayConfigQuery = useGatewayConfig(open ? gatewayId : null);
  const cacheOff =
    sectionEnforcement(gatewayConfigQuery, "cache") === "not_enforced";
  const tabs = editorTabsFor();
  // IP access control is off while the gateway does not run it, or names a
  // reason it cannot (no trusted proxy, so no client IP): its reason, or true.
  const ipAclReason = notEnforcedReason(gatewayConfigQuery, "ip_acl");
  const ipAclOff =
    ipAclReason ||
    sectionEnforcement(gatewayConfigQuery, "ip_acl") === "not_enforced";

  // Retain only over the exact pre-save read. A confirmed read or history
  // activation wins regardless of version ordering: rollback activates an
  // existing lower version rather than creating a higher one.
  const retained = savedSnapshot.current;
  const unchangedRead =
    retained &&
    initialConfig === retained.baseConfig &&
    initialConfigUpdatedAt === retained.baseReadAt;

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      const belongs =
        !initialConfig?.organization ||
        String(initialConfig.organization) === String(currentOrganizationId);
      const useRetained = belongs && unchangedRead;
      const sections = useRetained
        ? retained.sections
        : readSections(belongs ? initialConfig : null);
      setSaved(sections);
      setDraft(sections);
      setSavedVersion(
        useRetained
          ? retained.version
          : belongs
            ? initialConfig?.version
            : null,
      );
      setTab(editorTabId(defaultTab));
    }

    wasOpenRef.current = open;
  }, [
    open,
    defaultTab,
    initialConfig,
    currentOrganizationId,
    unchangedRead,
    retained,
  ]);

  // Active admission caps require legacy enforcement to stay on, and the
  // backend fences any budgets.enabled change behind the policy revision
  // (409 otherwise). Ignore a pending enabled edit in both the save and dirty
  // check: it must neither reach update-config nor strand the user behind a
  // disabled deactivation button. Caps are read from the newest read, never
  // from the draft's base, which is not refreshed while the editor is open:
  // the admission panel's own read (its load, Reload, or the read after an
  // admission change) until the page reads again, else the page's read of
  // this organization, else the base (an editor opened before its config
  // loaded). The panel's load and Reload count only over a loaded page read.
  // Normalize only the read, not the patch, to preserve stored alias spellings.
  const latestLoaded = useRef(null);
  latestLoaded.current = {
    config: initialConfig,
    readAt: initialConfigUpdatedAt,
  };
  const [admissionRead, setAdmissionRead] = useState(null);
  const loadedBelongs =
    Boolean(initialConfig) &&
    (!initialConfig.organization ||
      String(initialConfig.organization) === String(currentOrganizationId));
  const ownReadIsNewest =
    admissionRead !== null &&
    admissionRead.config === initialConfig &&
    admissionRead.readAt === initialConfigUpdatedAt &&
    (!admissionRead.panelRead || loadedBelongs);
  const policyBudgets = ownReadIsNewest
    ? admissionRead.budgets
    : loadedBelongs
      ? initialConfig.budgets
      : saved.budgets;
  const admissionActive =
    normalizeBudgets(policyBudgets).admission_caps_enabled === true;
  const budgetsForSave =
    admissionActive && draft.budgets.enabled !== saved.budgets.enabled
      ? { ...draft.budgets, enabled: saved.budgets.enabled }
      : draft.budgets;
  const admissionBlocked =
    saveConfig.isPending ||
    applyBusy ||
    JSON.stringify(budgetsForSave) !== JSON.stringify(saved.budgets) ||
    JSON.stringify(draft.routing) !== JSON.stringify(saved.routing);
  const admissionApplied = (config) => {
    const current = readSections(config);
    const rebase = (value) => ({
      ...value,
      budgets: current.budgets,
      routing: current.routing,
    });
    setSaved(rebase);
    setDraft(rebase);
    setSavedVersion(config?.version ?? null);
    savedSnapshot.current = null;
    // Newer than the page read current now (not when the change started),
    // and outranked by the page's next read.
    setAdmissionRead({ budgets: current.budgets, ...latestLoaded.current });
  };
  // Guard only: a routine panel read never rebases the draft, so pending
  // budget and routing edits stay. Ignore a read of another organization.
  const admissionPanelRead = (config) => {
    if (config && String(config.organization) !== String(currentOrganizationId))
      return;
    setAdmissionRead({
      budgets: readSections(config).budgets,
      panelRead: true,
      ...latestLoaded.current,
    });
  };

  const toSave = {
    ...draft,
    // A pending budgets on/off edit is ignored while admission caps are active.
    budgets: budgetsForSave,
    guardrails: guardrailsForSave(draft.guardrails, saved.guardrails),
  };
  const patch = buildConfigPatch(saved, toSave, SECTION_KEYS);
  const dirty = Object.keys(patch).length > 0;
  const receiptIsCurrent =
    receipt &&
    receipt.version != null &&
    savedVersion === receipt.version &&
    (initialConfig?.version === receipt.version || unchangedRead);
  const latestReceiptIsCurrent = useRef(receiptIsCurrent);
  latestReceiptIsCurrent.current = receiptIsCurrent;
  const publishReceipt = (next) => {
    if (!active.current) return;
    setReceipt(next);
    onSaved?.(next);
  };

  const handleSave = () => {
    if (!canEdit || admissionBusy || applyBusy || saveConfig.isPending) return;
    setSaveUnconfirmed(false);
    const submittedDraft = draft;
    saveConfig.mutate(
      { gatewayId, patch, saved },
      {
        onSuccess: (result) => {
          if (!active.current) return;
          if (!dirty) {
            onClose();
            return;
          }
          // update-config's version or explicit sync result confirms storage,
          // including a failed push. Older responses may omit the version.
          const version =
            Number.isInteger(result?.version) && result.version > 0
              ? result.version
              : null;
          if (version === null && typeof result?.gateway_synced !== "boolean") {
            setSaveUnconfirmed(true);
            return;
          }
          const hasRevision =
            Number.isInteger(result.policy_revision) &&
            result.policy_revision >= 0;
          const withRevision = (sections) =>
            hasRevision
              ? {
                  ...sections,
                  budgets: {
                    ...sections.budgets,
                    policy_revision: result.policy_revision,
                  },
                }
              : sections;
          const stored = withRevision(toSave);
          // Budgets and guardrails unchanged since Save are stored as saved,
          // including an ignored enabled edit; later edits are kept.
          const rebaseDraft = (current) =>
            withRevision({
              ...current,
              budgets: isEqual(current.budgets, submittedDraft.budgets)
                ? stored.budgets
                : current.budgets,
              guardrails: isEqual(current.guardrails, submittedDraft.guardrails)
                ? stored.guardrails
                : current.guardrails,
            });
          const remaining = rebaseDraft(latestDraft.current);
          setSaved(stored);
          setDraft(rebaseDraft);
          setSavedVersion(version);
          savedSnapshot.current = {
            version,
            sections: stored,
            baseConfig: initialConfig,
            baseReadAt: initialConfigUpdatedAt,
          };
          publishReceipt({
            organizationId: currentOrganizationId,
            gatewayId,
            version,
            applyState:
              result.gateway_synced === true
                ? "applied"
                : result.gateway_synced === false
                  ? "not_applied"
                  : "unconfirmed",
          });
          // Preserve the existing confirmed-success close, but never dismiss
          // a failed/unknown apply or discard edits made while Save was pending.
          if (result.gateway_synced === true && isEqual(remaining, stored))
            onClose();
        },
      },
    );
  };

  const isSaving = saveConfig.isPending;
  const index = Math.max(
    0,
    tabs.findIndex((item) => item.id === tab),
  );
  const shownTab = tabs[index].id;
  const enforced = tabs[index].enforced;

  return (
    <Dialog
      open={open}
      onClose={admissionBusy || isSaving || applyBusy ? undefined : onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{ sx: { maxWidth: 960, minHeight: "70vh" } }}
    >
      <DialogTitle>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography typography="m3" fontWeight="fontWeightMedium">
            Edit Organization Config
          </Typography>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {receipt && (
          <Alert
            severity={receipt.applyState === "applied" ? "success" : "warning"}
            sx={{ mb: 2 }}
          >
            <Stack spacing={1}>
              <Typography variant="subtitle2">
                {receipt.applyState === "applied"
                  ? "Saved · Applied to gateway"
                  : receipt.applyState === "not_applied"
                    ? "Saved · Not applied"
                    : "Saved, application not confirmed"}
              </Typography>
              <Typography variant="body2">
                {receipt.version == null
                  ? "Last save: version not reported."
                  : `Last save: version ${receipt.version}.`}
              </Typography>
              <Typography variant="body2">
                {dirty
                  ? "This receipt describes the stored configuration. Unsaved changes are not applied."
                  : "All edits stored."}
              </Typography>
              {receipt.applyState !== "applied" && (
                <>
                  <RetryConfigApply
                    key={receipt.version}
                    receipt={receipt}
                    disabled={
                      dirty ||
                      !receiptIsCurrent ||
                      !gatewayId ||
                      !canEdit ||
                      isSaving ||
                      admissionBusy
                    }
                    onApplied={(next) => {
                      if (latestReceiptIsCurrent.current) publishReceipt(next);
                    }}
                    onBusyChange={setApplyBusy}
                  />
                  {!receiptIsCurrent && (
                    <Typography variant="body2">
                      This receipt belongs to another saved version. Review the
                      current configuration before applying it.
                    </Typography>
                  )}
                </>
              )}
            </Stack>
          </Alert>
        )}
        {saveUnconfirmed && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Storage was not confirmed. Reload the organization configuration
            before trying to save again.
          </Alert>
        )}
        <CustomTabs
          value={index}
          onChange={(_, next) => {
            if (!admissionBusy && !isSaving) setTab(tabs[next].id);
          }}
          variant="scrollable"
          scrollButtons="auto"
          sx={{
            boxShadow: "none",
            mb: 2,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          {tabs.map((item) => (
            <Tab
              key={item.id}
              label={item.label}
              disabled={admissionBusy || isSaving}
            />
          ))}
        </CustomTabs>
        {/* NEW: site claims review required for this pointer. */}
        <Typography typography="s2" color="text.secondary" sx={{ mb: 2 }}>
          MCP settings are on the MCP page.
        </Typography>

        <Box sx={{ minHeight: 300 }}>
          {enforced && !(shownTab === "cache" && cacheOff) && (
            <SectionEnforcementNotes
              sections={enforced}
              gatewayConfigQuery={gatewayConfigQuery}
              sx={{ mb: 2 }}
            />
          )}
          {(shownTab === "a2a" || shownTab === "audit") && (
            <ReadOnlyOrgConfigTab
              section={shownTab}
              config={initialConfig?.[shownTab]}
            />
          )}
          {shownTab === "providers" && <ProviderConfigTab />}
          {shownTab === "guardrails" &&
            guardrailsAreOff(gatewayConfigQuery) && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {GUARDRAILS_OFF_BANNER}
              </Alert>
            )}
          {shownTab === "guardrails" && (
            <GuardrailConfigTab
              guardrails={draft.guardrails}
              saved={saved.guardrails}
              nodePipeline={gatewayConfigQuery?.data?.guardrail_pipeline}
              onChange={(guardrails) => setDraft((d) => ({ ...d, guardrails }))}
            />
          )}
          {shownTab === "routing" && (
            <RoutingConfigTab
              routing={draft.routing}
              onChange={(routing) => setDraft((d) => ({ ...d, routing }))}
              managedOffered={managedOffered}
            />
          )}
          {shownTab === "cache" && (
            <Stack spacing={2}>
              {cacheOff && (
                // NEW: site claims review required; shown only while off.
                <Typography typography="s2" color="text.secondary">
                  Response caching is off on this gateway. Changes you save
                  apply when response caching is turned on.
                </Typography>
              )}
              <CacheConfigTab
                cache={draft.cache}
                onChange={(cache) => setDraft((d) => ({ ...d, cache }))}
              />
            </Stack>
          )}
          {shownTab === "rate_limiting" && (
            <RateLimitingConfigTab
              rateLimiting={draft.rate_limiting}
              onChange={(rate_limiting) =>
                setDraft((d) => ({ ...d, rate_limiting }))
              }
            />
          )}
          {shownTab === "budgets" && (
            <>
              <AdmissionCapEditor
                blocked={admissionBlocked}
                onApplied={admissionApplied}
                onRead={admissionPanelRead}
                onBusyChange={setAdmissionBusy}
              />
              <Box
                component="fieldset"
                disabled={!canEdit || admissionBusy}
                sx={{ border: 0, p: 0, m: 0, minWidth: 0 }}
              >
                <BudgetsConfigTab
                  budgets={budgetsForSave}
                  admissionActive={admissionActive}
                  costTracking={draft.cost_tracking}
                  onBudgetsChange={(budgets) =>
                    setDraft((d) => ({ ...d, budgets }))
                  }
                  onCostTrackingChange={(cost_tracking) =>
                    setDraft((d) => ({ ...d, cost_tracking }))
                  }
                />
              </Box>
            </>
          )}
          {shownTab === "security" && (
            <SecurityConfigTab
              ipAcl={draft.ip_acl}
              privacy={draft.privacy}
              toolPolicy={draft.tool_policy}
              onIpAclChange={(ip_acl) => setDraft((d) => ({ ...d, ip_acl }))}
              onPrivacyChange={(privacy) =>
                setDraft((d) => ({ ...d, privacy }))
              }
              onToolPolicyChange={(tool_policy) =>
                setDraft((d) => ({ ...d, tool_policy }))
              }
              ipAclOff={ipAclOff || null}
            />
          )}
          {shownTab === "alerting" && (
            <AlertingConfigTab
              alerting={draft.alerting}
              onChange={(alerting) => setDraft((d) => ({ ...d, alerting }))}
            />
          )}
          {shownTab === "model_database" && (
            <ModelDatabaseConfigTab
              modelDatabase={draft.model_database}
              onChange={(model_database) =>
                setDraft((d) => ({ ...d, model_database }))
              }
              managedOffered={managedOffered}
            />
          )}
          {shownTab === "model_map" && (
            <ModelMapConfigTab
              gatewayId={gatewayId}
              modelMap={draft.model_map}
              onChange={(model_map) => setDraft((d) => ({ ...d, model_map }))}
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button
          onClick={onClose}
          disabled={isSaving || admissionBusy || applyBusy}
        >
          Cancel
        </Button>
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            color="primary"
            onClick={handleSave}
            disabled={
              isSaving ||
              admissionBusy ||
              applyBusy ||
              !canEdit ||
              (Boolean(receipt) && !dirty)
            }
            startIcon={isSaving ? <CircularProgress size={16} /> : null}
          >
            {isSaving ? "Saving..." : "Save & Activate"}
          </Button>
        </GatewayAdminOnly>
      </DialogActions>
    </Dialog>
  );
};

// Remount the draft, receipt and in-flight callbacks together on a scope
// change, while retaining them across explicit close/reopen in the same scope.
export default function OrgConfigEditor(props) {
  const { gatewayId, managedOffered } = useGatewayContext();
  const { currentOrganizationId } = useOrganization();
  const receipt = props.savedReceipt;
  const savedReceipt =
    receipt?.organizationId === currentOrganizationId &&
    receipt?.gatewayId === gatewayId
      ? receipt
      : null;
  return (
    <ScopedOrgConfigEditor
      key={JSON.stringify([currentOrganizationId, gatewayId])}
      {...props}
      savedReceipt={savedReceipt}
      gatewayId={gatewayId}
      managedOffered={managedOffered}
      currentOrganizationId={currentOrganizationId}
    />
  );
}
