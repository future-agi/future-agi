import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import React, { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import axiosInstance, { endpoints } from "src/utils/axios";

import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useGatewayContext } from "../context/useGatewayContext";
import {
  useGatewayConfig,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { isSettingsTabHidden } from "../shell/page-status";
import BatchJobsTab from "./batch/BatchJobsTab";
import FullConfigTab from "./FullConfigTab";
import GeneralTab from "./GeneralTab";
import OrgConfigSection from "./OrgConfigSection";
import { isPermissionError } from "./useSettingsWriteAccess";

const ROOT = paths.dashboard.gateway.settings;

// The page's tabs, by URL slug ("" is the page itself). A tab hidden in
// shell/page-status.js is left out, and its URL opens General.
const TABS = [
  { slug: "", label: "General" },
  { slug: "org-config", label: "Org config" },
  { slug: "batch-jobs", label: "Batch jobs" },
  { slug: "full-config", label: "Full config" },
].filter((tab) => !isSettingsTabHidden(tab.slug));

// Tabs live in ?tab=, not in a /settings/<tab> path: the dashboard nav opens
// the Future AGI settings sidebar on any path containing "/settings/"
// (layouts/dashboard/nav-vertical.jsx), which would replace the gateway rail.
const tabPath = (slug) => (slug ? `${ROOT}?tab=${slug}` : ROOT);

function SettingsSkeleton() {
  return (
    <Stack spacing={2} data-testid="gateway-settings-loading">
      <Skeleton variant="rounded" height={32} width={280} />
      <Skeleton variant="rounded" height={220} />
      <Skeleton variant="rounded" height={160} />
    </Stack>
  );
}

const healthCheckError = (error) => {
  const remedy = getGatewayAuthenticationErrorMessage(error);
  if (remedy) return remedy;
  const answer = error?.response?.data?.result || error?.response?.data;
  return (
    (typeof answer?.error === "string" && answer.error) ||
    (typeof answer === "string" && answer) ||
    error?.message ||
    "health check failed"
  );
};

// Gateway settings: the gateway itself and what it reports (General), the
// organization's gateway config (Org config), the console's batch jobs
// (Batch jobs) and the config as the gateway receives it (Full config).
export default function GatewaySettingsSection() {
  // An old /settings/<tab> link (the route keeps :tab) moves to ?tab=.
  const { tab: legacySlug } = useParams();
  const [searchParams] = useSearchParams();
  const slug = legacySlug || searchParams.get("tab") || "";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // Health Check and Reload Config post to the gateway: Owner or Admin only,
  // as on Providers, API keys and Budgets.
  const { canEdit } = useGatewayAdmin();
  const { gateway, gatewayId, isLoading: gatewayLoading } = useGatewayContext();
  const {
    data: config,
    isLoading: configLoading,
    error: configError,
    refetch,
  } = useGatewayConfig(gatewayId);
  const reload = useReloadConfig();
  const [health, setHealth] = useState(null);

  const index = Math.max(
    0,
    TABS.findIndex((tab) => tab.slug === slug),
  );
  const current = TABS[index].slug;
  const known = TABS.some((tab) => tab.slug === slug);
  const hidden = isSettingsTabHidden(slug);
  useEffect(() => {
    if (hidden) navigate(ROOT, { replace: true });
    else if (legacySlug) {
      navigate(tabPath(known ? legacySlug : ""), { replace: true });
    }
  }, [hidden, known, legacySlug, navigate]);

  // A check records its time and outcome either way, so the gateway list and
  // the config (its last_health_check) are read again after it.
  const refreshAfterHealthCheck = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["agentcc-gateways"] });
    refetch?.();
  }, [queryClient, refetch]);

  const healthCheck = useMutation({
    mutationFn: async (id) => {
      const { data } = await axiosInstance.post(
        endpoints.gateway.healthCheck(id),
        {},
      );
      return data?.result;
    },
    onSuccess: (result) => {
      setHealth({ ok: true, at: new Date(), result: result || null });
      refreshAfterHealthCheck();
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: (error) => {
      const denied = isPermissionError(error);
      setHealth({
        ok: false,
        denied,
        at: new Date(),
        error: healthCheckError(error),
      });
      if (!denied) refreshAfterHealthCheck();
      enqueueSnackbar(
        getGatewayAuthenticationErrorMessage(error) ||
          (denied ? "Health check not authorized" : "Health check failed"),
        { variant: "error" },
      );
    },
  });

  const reloadConfig = useCallback(() => {
    reload.mutate(gatewayId, {
      onSuccess: () =>
        enqueueSnackbar("Configuration reloaded", { variant: "success" }),
      onError: (error) =>
        enqueueSnackbar(
          getGatewayAuthenticationErrorMessage(error) ||
            (isPermissionError(error)
              ? "Config reload not authorized"
              : "Failed to reload config"),
          { variant: "error" },
        ),
    });
  }, [gatewayId, reload]);

  // Org config and batch status have independent reads. Optional batch
  // configuration facts must not prevent recovery of an existing job.
  const readsGatewayConfig = current === "" || current === "full-config";
  const loading = gatewayLoading || (readsGatewayConfig && configLoading);
  const configFailed = readsGatewayConfig && Boolean(configError);

  return (
    <GatewayPageFrame
      title="Gateway settings"
      description="The gateway's endpoint, health and settings, your organization's gateway config as it is pushed to the gateway, and batch jobs."
      actions={
        <>
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="outlined"
              onClick={() => healthCheck.mutate(gatewayId)}
              disabled={!canEdit || !gatewayId || healthCheck.isPending}
            >
              {healthCheck.isPending ? "Checking…" : "Health Check"}
            </Button>
          </GatewayAdminOnly>
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="outlined"
              onClick={reloadConfig}
              disabled={!canEdit || !gatewayId || reload.isPending}
            >
              {reload.isPending ? "Reloading…" : "Reload Config"}
            </Button>
          </GatewayAdminOnly>
        </>
      }
    >
      <CustomTabs
        value={index}
        onChange={(_, next) =>
          navigate(tabPath(TABS[next].slug), { replace: true })
        }
        variant="scrollable"
        scrollButtons={false}
        sx={{
          boxShadow: "none",
          borderBottom: 1,
          borderColor: "divider",
          minHeight: 40,
        }}
      >
        {TABS.map((tab) => (
          <Tab key={tab.label} label={tab.label} sx={{ minHeight: 40 }} />
        ))}
      </CustomTabs>

      {loading && <SettingsSkeleton />}
      {!loading && configFailed && (
        <GatewayEmptyState
          icon="settings"
          title="Couldn't load this gateway's settings"
          description={
            getGatewayAuthenticationErrorMessage(configError) ||
            configError?.message ||
            "The settings request failed."
          }
          actionLabel="Retry"
          onAction={() => refetch?.()}
        />
      )}
      {!loading && !configFailed && (
        <>
          {current === "" && (
            <GeneralTab gateway={gateway} config={config} health={health} />
          )}
          {current === "org-config" && <OrgConfigSection />}
          {current === "batch-jobs" && (
            <BatchJobsTab
              submissionAvailable={gateway?.delegatedBatchAvailable !== false}
              config={config}
              configLoading={configLoading}
              configError={configError}
              onRetryConfig={refetch}
              gatewayId={gatewayId}
            />
          )}
          {current === "full-config" && <FullConfigTab config={config} />}
        </>
      )}
    </GatewayPageFrame>
  );
}
