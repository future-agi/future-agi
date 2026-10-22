/* eslint-disable react/prop-types */
import React, { useState, useEffect, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Tabs,
  Tab,
  Box,
  TextField,
  Stack,
  Typography,
  CircularProgress,
} from "@mui/material";
import ProviderConfigTab from "./ProviderConfigTab";
import GuardrailConfigTab from "./GuardrailConfigTab";
import RoutingConfigTab from "./RoutingConfigTab";
import CacheConfigTab from "./CacheConfigTab";
import RateLimitingConfigTab from "./RateLimitingConfigTab";
import BudgetsConfigTab from "./BudgetsConfigTab";
import SecurityConfigTab from "./SecurityConfigTab";
import AlertingConfigTab from "./AlertingConfigTab";
import MCPConfigTab from "./MCPConfigTab";
import A2AConfigTab from "./A2AConfigTab";
import AuditConfigTab from "./AuditConfigTab";
import ModelDatabaseConfigTab from "./ModelDatabaseConfigTab";
import ModelMapConfigTab from "./ModelMapConfigTab";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

const OrgConfigEditor = ({
  open,
  onClose,
  onSave,
  initialConfig,
  isSaving,
  defaultTab = 0,
}) => {
  const [tab, setTab] = useState(defaultTab);
  const wasOpenRef = useRef(false);
  // Every member may open the editor to read the config; saving it is Owner or
  // Admin only.
  const { canEdit } = useGatewayAdmin();
  const [draft, setDraft] = useState({
    providers: {},
    guardrails: {},
    routing: {},
    cache: {},
    rate_limiting: {},
    budgets: {},
    cost_tracking: {},
    ip_acl: {},
    alerting: {},
    privacy: {},
    tool_policy: {},
    mcp: {},
    a2a: {},
    audit: {},
    model_database: {},
    model_map: {},
  });
  const [changeDescription, setChangeDescription] = useState("");

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setDraft({
        providers: initialConfig?.providers || {},
        guardrails: initialConfig?.guardrails || {},
        routing: initialConfig?.routing || {},
        cache: initialConfig?.cache || {},
        rate_limiting:
          initialConfig?.rate_limiting || initialConfig?.rateLimiting || {},
        budgets: initialConfig?.budgets || {},
        cost_tracking:
          initialConfig?.cost_tracking || initialConfig?.costTracking || {},
        ip_acl: initialConfig?.ip_acl || initialConfig?.ipAcl || {},
        alerting: initialConfig?.alerting || {},
        privacy: initialConfig?.privacy || {},
        tool_policy:
          initialConfig?.tool_policy || initialConfig?.toolPolicy || {},
        mcp: initialConfig?.mcp || {},
        a2a: initialConfig?.a2a || {},
        audit: initialConfig?.audit || {},
        model_database:
          initialConfig?.model_database || initialConfig?.modelDatabase || {},
        model_map: initialConfig?.model_map || initialConfig?.modelMap || {},
      });
      setChangeDescription("");
      setTab(defaultTab);
    }

    wasOpenRef.current = open;
  }, [open, defaultTab, initialConfig]);

  const handleSave = () => {
    onSave({
      providers: draft.providers,
      guardrails: draft.guardrails,
      routing: draft.routing,
      cache: draft.cache,
      rate_limiting: draft.rate_limiting,
      budgets: draft.budgets,
      cost_tracking: draft.cost_tracking,
      ip_acl: draft.ip_acl,
      alerting: draft.alerting,
      privacy: draft.privacy,
      tool_policy: draft.tool_policy,
      mcp: draft.mcp,
      a2a: draft.a2a,
      audit: draft.audit,
      model_database: draft.model_database,
      model_map: draft.model_map,
      change_description: changeDescription || "Updated org config",
    });
  };

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
          <Tab label="MCP" />
          <Tab label="A2A" />
          <Tab label="Audit" />
          <Tab label="Model Database" />
          <Tab label="Model Map" />
        </Tabs>

        <Box sx={{ minHeight: 300 }}>
          {tab === 0 && (
            <ProviderConfigTab
              providers={draft.providers}
              onChange={(providers) => setDraft((d) => ({ ...d, providers }))}
            />
          )}
          {tab === 1 && (
            <GuardrailConfigTab
              guardrails={draft.guardrails}
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
            <MCPConfigTab
              mcp={draft.mcp}
              onChange={(mcp) => setDraft((d) => ({ ...d, mcp }))}
            />
          )}
          {tab === 9 && (
            <A2AConfigTab
              a2a={draft.a2a}
              onChange={(a2a) => setDraft((d) => ({ ...d, a2a }))}
            />
          )}
          {tab === 10 && (
            <AuditConfigTab
              audit={draft.audit}
              onChange={(audit) => setDraft((d) => ({ ...d, audit }))}
            />
          )}
          {tab === 11 && (
            <ModelDatabaseConfigTab
              modelDatabase={draft.model_database}
              onChange={(model_database) =>
                setDraft((d) => ({ ...d, model_database }))
              }
            />
          )}
          {tab === 12 && (
            <ModelMapConfigTab
              modelMap={draft.model_map}
              onChange={(model_map) => setDraft((d) => ({ ...d, model_map }))}
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <TextField
          size="small"
          placeholder="Change description (optional)"
          value={changeDescription}
          onChange={(e) => setChangeDescription(e.target.value)}
          sx={{ flex: 1, mr: 2 }}
        />
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
