import React, { Suspense } from "react";
import { Helmet } from "react-helmet-async";
import { Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";
import {
  Box,
  IconButton,
  Skeleton,
  Stack,
  Tab,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { CONNECTED_AGENTS_PATH } from "../../agentsTabs";
import AgentDetailHeader, {
  AgentDetailHeaderSkeleton,
} from "./AgentDetailHeader";
import AgentVersionSelect from "./AgentVersionSelect";
import {
  DETAIL_TABS,
  UNRELEASED_TABS,
  agentDetailPath,
} from "./agentDetailTabs";
import useAgentRoute from "./useAgentRoute";
import ContentSkeleton from "src/components/content-skeleton";

const errorText = (error) =>
  error?.message || error?.detail || "Couldn't load this agent";

/**
 * A connected agent: header and tabs. Each tab is its own route rendered into
 * the Outlet. Every agent gets Versions and Environments — there's no field
 * yet that tells an environment-built agent from an older one — and each tab
 * shows what its API returns.
 */
export default function AgentDetailLayout() {
  const navigate = useNavigate();
  const { search } = useLocation();
  const { agentId, agent, query, versionId, selectVersion } = useAgentRoute();
  const match = useMatch(`${CONNECTED_AGENTS_PATH}/:agentId/:tab/*`);
  const tab = match?.params.tab;

  const back = () => navigate(CONNECTED_AGENTS_PATH);

  // A failed background refetch keeps the loaded page; only a first load
  // that fails shows the error.
  if (query.isError && !agent) {
    return (
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ p: 2 }}>
        <IconButton size="small" onClick={back} aria-label="Back">
          <Iconify icon="solar:alt-arrow-left-linear" width={18} />
        </IconButton>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {errorText(query.error)}
        </Typography>
      </Stack>
    );
  }

  // Counts arrive with the agent; until then the tabs show their names.
  const tabLabel = (t) => {
    if (!agent) return t.label;
    if (t.id === "versions") return `Versions ${agent.versionCount}`;
    if (t.id === "environments")
      return `Environments ${agent.environmentCount}`;
    return t.label;
  };

  return (
    <>
      <Helmet>
        <title>{agent ? `${agent.name} · Agents` : "Agents"}</title>
      </Helmet>
      <Box
        sx={{
          px: 2,
          pt: 2,
          height: "100%",
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {agent ? (
          <AgentDetailHeader
            agent={agent}
            onBack={back}
            onOpenEnvironments={() =>
              navigate(`${agentDetailPath(agentId, "environments")}${search}`)
            }
            action={
              versionId ? (
                <AgentVersionSelect
                  agentId={agentId}
                  value={versionId}
                  onChange={selectVersion}
                />
              ) : null
            }
          />
        ) : (
          <AgentDetailHeaderSkeleton onBack={back} />
        )}
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
            mb: 2,
            flexShrink: 0,
          }}
        >
          <CustomTabs
            sx={{ boxShadow: "none" }}
            value={DETAIL_TABS.some((t) => t.id === tab) ? tab : false}
            onChange={(_, v) =>
              navigate(`${agentDetailPath(agentId, v)}${search}`)
            }
            // With a short chip on each unbuilt tab the strip is about 745 px
            // at two-digit counts, inside the 792 px a 1024 px window leaves
            // it, so no arrows show from 1024 up; below that, arrows appear
            // only when the strip is wider than the page, so the last tab
            // stays reachable.
            variant="scrollable"
            scrollButtons="auto"
            allowScrollButtonsMobile
          >
            {DETAIL_TABS.map((t) => (
              <Tab key={t.id} value={t.id} label={tabLabel(t)} disableRipple />
            ))}
            {UNRELEASED_TABS.map((label) => (
              <Tab
                key={label}
                value={label}
                disabled
                disableRipple
                // MUI fades a disabled tab to 48%, which took the label and its
                // chip to about 2.3:1. Keep them at full strength: the theme
                // already gives an unselected tab secondary text (above 4.5:1
                // in both themes); the chip and the missing hover still mark
                // the tab as off.
                sx={{
                  "&.Mui-disabled": { opacity: 1 },
                }}
                label={
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <span>{label}</span>
                    {/* The whole line on each of the three made the strip
                        about 1090 px, cut a chip mid-word at 1024 and
                        1280 px and hid the last tab behind an arrow; the
                        short chip keeps each tab and its name whole, with
                        the line in its tooltip. */}
                    <NotInReleaseChip short />
                  </Stack>
                }
              />
            ))}
          </CustomTabs>
        </Box>
        <Suspense
          fallback={
            <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
              <ContentSkeleton variant="panel" />
            </Box>
          }
        >
          {/* The tab gets the height left below the tabs; taller content
              still scrolls the page. The bottom gap lives on the tab itself,
              so it shows after content that overflows. */}
          <Box sx={{ flex: "1 1 0", minHeight: 0, "& > *": { pb: 2 } }}>
            <Outlet />
          </Box>
        </Suspense>
      </Box>
    </>
  );
}
