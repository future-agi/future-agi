import { useEffect } from "react";
import { Box, Stack, Tab, Tabs, Typography } from "@mui/material";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import { resetEnvironmentsEntryState } from "./store/useEnvironmentsStore";
import BuildEnvironmentTab from "./BuildEnvironmentTab";
import MyEnvironmentsTab from "./MyEnvironmentsTab";
import PersonasTab from "./PersonasTab";
import { ENTRY_TAB, ENVIRONMENTS_HEADER, ENV_TABS_SX } from "./environmentOptions";

const TAB_PANELS = {
  [ENTRY_TAB.BUILD]: BuildEnvironmentTab,
  [ENTRY_TAB.MY]: MyEnvironmentsTab,
  [ENTRY_TAB.PERSONAS]: PersonasTab,
};

export default function EnvironmentsHome() {
  const { tab, setTab } = useEnvironmentsTab();
  const TabPanel = TAB_PANELS[tab];

  const onTabChange = (next) => {
    // Keeps the event the old Personas menu item fired.
    if (next === ENTRY_TAB.PERSONAS) {
      trackEvent(Events.navigationPersonasClicked, {
        [PropertyName.click]: true,
      });
    }
    setTab(next);
  };

  // The draft must survive the hop to the build page, so the matrix starts clean
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
      </Stack>

      {/* Tab rail — matches the product's other tab bars (HarnessDetail): tabs
          flush, padding-spaced, one continuous underline against the divider. */}
      <Box sx={{ px: 2, borderBottom: 1, borderColor: "divider", flexShrink: 0 }}>
        <Tabs value={tab} onChange={(_, v) => onTabChange(v)} sx={ENV_TABS_SX}>
          <Tab value={ENTRY_TAB.BUILD} disableRipple label="Build environment" />
          <Tab value={ENTRY_TAB.MY} disableRipple label="My Environments" />
          <Tab value={ENTRY_TAB.PERSONAS} disableRipple label="Personas" />
        </Tabs>
      </Box>

      <TabPanel />
    </Box>
  );
}
