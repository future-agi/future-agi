import { useEffect, useState } from "react";
import { Box, Button, Stack, Tab, Tabs, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import { resetEnvironmentsEntryState } from "./store/useEnvironmentsStore";
import MyEnvironmentsTab from "./MyEnvironmentsTab";
import { useMyEnvironments } from "src/api/simulate-environments/environments";
import { DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import PersonasTab from "./PersonasTab";
import BuildEnvironmentDrawer from "./buildDrawer/BuildEnvironmentDrawer";
import { ENTRY_TAB, ENVIRONMENTS_HEADER, ENV_TABS_SX } from "./environmentOptions";

const TAB_PANELS = {
  [ENTRY_TAB.MY]: MyEnvironmentsTab,
  [ENTRY_TAB.PERSONAS]: PersonasTab,
};

// "My Environments 37": how many there are in all, from the unfiltered first
// page, which the tab requests anyway (same query, so no extra request there).
function MyEnvironmentsLabel() {
  const { data, isSuccess } = useMyEnvironments({ page: 0, pageSize: DEFAULT_PAGE_SIZE });
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} component="span">
      <span>My Environments</span>
      {isSuccess && (
        <Box
          component="span"
          sx={{ typography: "s2", color: "text.secondary", fontVariantNumeric: "tabular-nums" }}
        >
          {data?.total ?? 0}
        </Box>
      )}
    </Stack>
  );
}

export default function EnvironmentsHome() {
  const { tab, setTab, opensBuild } = useEnvironmentsTab();
  const TabPanel = TAB_PANELS[tab];
  const [buildOpen, setBuildOpen] = useState(opensBuild);

  // A link to the old Build tab opens the drawer over My Environments.
  useEffect(() => {
    if (!opensBuild) return;
    setBuildOpen(true);
    setTab(ENTRY_TAB.MY);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opensBuild]);

  const onTabChange = (next) => {
    // Keeps the event the old Personas menu item fired.
    if (next === ENTRY_TAB.PERSONAS) {
      trackEvent(Events.navigationPersonasClicked, {
        [PropertyName.click]: true,
      });
    }
    setTab(next);
  };

  // The draft must survive the hop to the workspace, so each visit starts clean
  // on mount instead of clearing on unmount. Only the entry/build slice is
  // cleared — adopted workspaces persist.
  useEffect(() => {
    resetEnvironmentsEntryState();
  }, []);

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ sm: "flex-end" }}
        spacing={2}
        sx={{ px: 2, pt: 2, pb: 1.5, flexShrink: 0 }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.5} flex={1} minWidth={0}>
          <Box>
            <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
              {ENVIRONMENTS_HEADER.title}
            </Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {ENVIRONMENTS_HEADER.subtitle}
            </Typography>
          </Box>
        </Stack>
        <Button
          size="small"
          variant="contained"
          color="primary"
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          onClick={() => setBuildOpen(true)}
          sx={{ flexShrink: 0 }}
        >
          Build environment
        </Button>
      </Stack>

      {/* Tab rail — matches the product's other tab bars (HarnessDetail): tabs
          flush, padding-spaced, one continuous underline against the divider. */}
      <Box sx={{ px: 2, borderBottom: 1, borderColor: "divider", flexShrink: 0 }}>
        <Tabs value={tab} onChange={(_, v) => onTabChange(v)} sx={ENV_TABS_SX}>
          <Tab value={ENTRY_TAB.MY} disableRipple label={<MyEnvironmentsLabel />} />
          <Tab value={ENTRY_TAB.PERSONAS} disableRipple label="Personas" />
        </Tabs>
      </Box>

      <TabPanel onBuild={() => setBuildOpen(true)} />
      <BuildEnvironmentDrawer open={buildOpen} onClose={() => setBuildOpen(false)} />
    </Box>
  );
}
