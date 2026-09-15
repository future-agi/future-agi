import { useEffect } from "react";
import { Box, Stack, Tab, Tabs, Typography } from "@mui/material";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import { resetEnvironmentsStore } from "./store/useEnvironmentsStore";
import BuildEnvironmentTab from "./BuildEnvironmentTab";
import MyEnvironmentsTab from "./MyEnvironmentsTab";
import { ENTRY_TAB, ENVIRONMENTS_HEADER, ENV_TABS_SX } from "./environmentOptions";

export default function EnvironmentsHome() {
  const { tab, setTab } = useEnvironmentsTab();

  // Client state (open entry card + draft) is per-visit; clear it on unmount so
  // the next visit starts clean.
  useEffect(() => () => resetEnvironmentsStore(), []);

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
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={ENV_TABS_SX}>
          <Tab value={ENTRY_TAB.BUILD} disableRipple label="Build environment" />
          <Tab value={ENTRY_TAB.MY} disableRipple label="My Environments" />
        </Tabs>
      </Box>

      {tab === ENTRY_TAB.BUILD ? <BuildEnvironmentTab /> : <MyEnvironmentsTab />}
    </Box>
  );
}
