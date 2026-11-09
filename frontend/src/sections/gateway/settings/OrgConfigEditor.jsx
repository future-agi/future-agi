/* eslint-disable react/prop-types */
import React, { useState, useEffect, useRef } from "react";
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
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
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

const OrgConfigEditor = ({ open, onClose, initialConfig, defaultTab = 0 }) => {
  // Whether the deployment offers managed models: the Routing and Model
  // Database tabs explain managed routing and prices only where it does.
  const { gatewayId, managedOffered } = useGatewayContext();
  // Anyone may open the editor to read the config; saving it is Owner or
  // Admin only (useGatewayAdmin).
  const { canEdit } = useGatewayAdmin();
  const saveConfig = useConfigPatch({
    action: "Saving org config",
    successMessage: "Org config saved",
  });
  const [tab, setTab] = useState(() => editorTabId(defaultTab));
  const wasOpenRef = useRef(false);
  // What the draft was opened from: the save sends only what differs from it.
  const [saved, setSaved] = useState(() => readSections(null));
  const [draft, setDraft] = useState(() => readSections(null));
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

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      const sections = readSections(initialConfig);
      setSaved(sections);
      setDraft(sections);
      setTab(editorTabId(defaultTab));
    }

    wasOpenRef.current = open;
  }, [open, defaultTab, initialConfig]);

  const handleSave = () => {
    const toSave = {
      ...draft,
      guardrails: guardrailsForSave(draft.guardrails, saved.guardrails),
    };
    saveConfig.mutate(
      {
        gatewayId,
        patch: buildConfigPatch(saved, toSave, SECTION_KEYS),
        saved,
      },
      { onSuccess: () => onClose() },
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
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{ sx: { minHeight: "70vh" } }}
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
        <CustomTabs
          value={index}
          onChange={(_, next) => setTab(tabs[next].id)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}
        >
          {tabs.map((item) => (
            <Tab key={item.id} label={item.label} />
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
            <BudgetsConfigTab
              budgets={draft.budgets}
              costTracking={draft.cost_tracking}
              onBudgetsChange={(budgets) =>
                setDraft((d) => ({ ...d, budgets }))
              }
              onCostTrackingChange={(cost_tracking) =>
                setDraft((d) => ({ ...d, cost_tracking }))
              }
            />
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
              modelMap={draft.model_map}
              onChange={(model_map) => setDraft((d) => ({ ...d, model_map }))}
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            color="primary"
            onClick={handleSave}
            disabled={isSaving || !canEdit}
            startIcon={isSaving ? <CircularProgress size={16} /> : null}
          >
            {isSaving ? "Saving..." : "Save & Activate"}
          </Button>
        </GatewayAdminOnly>
      </DialogActions>
    </Dialog>
  );
};

export default OrgConfigEditor;
