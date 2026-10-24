/* eslint-disable react/prop-types */
import React, { useState, useEffect, useRef } from "react";
import {
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Tabs,
  Tab,
  Box,
  Stack,
  Typography,
  CircularProgress,
} from "@mui/material";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
import {
  buildConfigPatch,
  guardrailsForSave,
  useConfigPatch,
} from "../hooks/useConfigPatch";
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
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

// The sections the editor edits, each with the camelCase name a payload
// may carry. Providers are not among them: they are managed on the Providers
// page, and update-config does not accept them. The org audit, A2A and MCP
// sections have no tab (the gateway applies no org audit or A2A section and
// connects no organization's MCP server) and are not here either, so a save
// never sends, rewrites or drops a stored one.
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
];
const SECTION_KEYS = SECTIONS.map(([key]) => key);

// The gateway's /-/config reports, in org_sections_enforced,
// which org sections this node runs the plugin for; Django passes the list
// through in the config read. The tabs below edit those sections.
const ENFORCED_SECTION_LABELS = {
  alerting: "Alerting",
  budgets: "Budgets",
  cache: "Cache",
  ip_acl: "IP ACL",
  rate_limiting: "Rate Limiting",
  tool_policy: "Tool Policy",
};

// "enforced" | "not_enforced" | "unknown", or null while the read is pending.
function sectionEnforcement(gatewayConfigQuery, section) {
  if (gatewayConfigQuery?.isLoading) return null;
  const reported = gatewayConfigQuery?.data?.org_sections_enforced;
  if (!Array.isArray(reported)) return "unknown";
  return reported.includes(section) ? "enforced" : "not_enforced";
}

// The gateway's own reason for not enforcing a section, from
// org_sections_not_enforced (ip_acl without a trusted proxy: "client IP
// unknown (trusted_proxies not set)"), or null when it gives none.
function notEnforcedReason(gatewayConfigQuery, section) {
  const reasons = gatewayConfigQuery?.data?.org_sections_not_enforced;
  const reason =
    reasons && typeof reasons === "object" ? reasons[section] : null;
  return typeof reason === "string" && reason ? reason : null;
}

function enforcementSentence(section, state, reason) {
  const label = ENFORCED_SECTION_LABELS[section];
  if (state === "unknown") {
    return `${label}: enforcement unknown. This gateway does not report which organization settings it enforces.`;
  }
  if (reason) {
    return `${label}: Not enforced on this gateway: ${reason}. These settings are saved but not applied to requests.`;
  }
  if (section === "cache") {
    return "Cache: Not enforced on this gateway. Response caching is off on this gateway, so these settings are saved but not applied to requests.";
  }
  return `${label}: Not enforced on this gateway. This gateway does not run the ${label} plugin, so these settings are saved but not applied to requests.`;
}

// The note above a tab whose sections the gateway may not enforce. Nothing
// while the config read is pending, nothing for an enforced section, and
// never a claim of enforcement from anything but the gateway's own list.
function EnforcementNotes({ sections, gatewayConfigQuery }) {
  const notes = sections
    .map((section) => [
      section,
      sectionEnforcement(gatewayConfigQuery, section),
    ])
    .filter(([, state]) => state === "unknown" || state === "not_enforced");
  if (notes.length === 0) return null;
  return (
    <Stack spacing={1} sx={{ mb: 2 }}>
      {notes.map(([section, state]) => (
        <Alert
          key={section}
          severity={state === "unknown" ? "info" : "warning"}
          variant="outlined"
        >
          {enforcementSentence(
            section,
            state,
            notEnforcedReason(gatewayConfigQuery, section),
          )}
        </Alert>
      ))}
    </Stack>
  );
}

function readSections(config) {
  return Object.fromEntries(
    SECTIONS.map(([key, alias]) => [
      key,
      config?.[key] || (alias && config?.[alias]) || {},
    ]),
  );
}

// The org sections each tab edits, by tab index (Cache 3, Rate Limiting 4,
// Budgets 5, Security 6, Alerting 7).
const TAB_ENFORCED_SECTIONS = {
  3: ["cache"],
  4: ["rate_limiting"],
  5: ["budgets"],
  6: ["ip_acl", "tool_policy"],
  7: ["alerting"],
};

const OrgConfigEditor = ({ open, onClose, initialConfig, defaultTab = 0 }) => {
  const { gatewayId } = useGatewayContext();
  // Anyone may open the editor to read the config; saving it is Owner or
  // Admin only (useGatewayAdmin).
  const { canEdit } = useGatewayAdmin();
  const saveConfig = useConfigPatch({
    action: "Saving org config",
    successMessage: "Org config saved",
  });
  const [tab, setTab] = useState(defaultTab);
  const wasOpenRef = useRef(false);
  // What the draft was opened from: the save sends only what differs from it.
  const [saved, setSaved] = useState(() => readSections(null));
  const [draft, setDraft] = useState(() => readSections(null));
  const gatewayConfigQuery = useGatewayConfig(open ? gatewayId : null);
  const cacheOff =
    sectionEnforcement(gatewayConfigQuery, "cache") === "not_enforced";

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      const sections = readSections(initialConfig);
      setSaved(sections);
      setDraft(sections);
      setTab(defaultTab);
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
          <Typography variant="h6">Edit Organization Config</Typography>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ mb: 2, borderBottom: 1, borderColor: "divider" }}
        >
          <Tab label="Providers" />
          <Tab label="Guardrails" />
          <Tab label="Routing" />
          <Tab label="Cache" />
          <Tab label="Rate Limiting" />
          <Tab label="Budgets" />
          <Tab label="Security" />
          <Tab label="Alerting" />
          <Tab label="Model Database" />
          <Tab label="Model Map" />
        </Tabs>

        <Box sx={{ minHeight: 300 }}>
          {TAB_ENFORCED_SECTIONS[tab] && (
            <EnforcementNotes
              sections={TAB_ENFORCED_SECTIONS[tab]}
              gatewayConfigQuery={gatewayConfigQuery}
            />
          )}
          {tab === 0 && <ProviderConfigTab />}
          {tab === 1 && (
            <GuardrailConfigTab
              guardrails={draft.guardrails}
              saved={saved.guardrails}
              nodePipeline={gatewayConfigQuery?.data?.guardrail_pipeline}
              onChange={(guardrails) => setDraft((d) => ({ ...d, guardrails }))}
            />
          )}
          {tab === 2 && (
            <RoutingConfigTab
              routing={draft.routing}
              onChange={(routing) => setDraft((d) => ({ ...d, routing }))}
            />
          )}
          {tab === 3 && (
            <CacheConfigTab
              cache={draft.cache}
              disabled={cacheOff}
              onChange={(cache) => setDraft((d) => ({ ...d, cache }))}
            />
          )}
          {tab === 4 && (
            <RateLimitingConfigTab
              rateLimiting={draft.rate_limiting}
              onChange={(rate_limiting) =>
                setDraft((d) => ({ ...d, rate_limiting }))
              }
            />
          )}
          {tab === 5 && (
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
          {tab === 6 && (
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
            />
          )}
          {tab === 7 && (
            <AlertingConfigTab
              alerting={draft.alerting}
              onChange={(alerting) => setDraft((d) => ({ ...d, alerting }))}
            />
          )}
          {tab === 8 && (
            <ModelDatabaseConfigTab
              modelDatabase={draft.model_database}
              onChange={(model_database) =>
                setDraft((d) => ({ ...d, model_database }))
              }
            />
          )}
          {tab === 9 && (
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
