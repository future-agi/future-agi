import { useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, Button, Stack, Tab, Tabs } from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import useEnvironmentsTab from "./hooks/useEnvironmentsTab";
import { resetEnvironmentsEntryState } from "./store/useEnvironmentsStore";
import MyEnvironmentsTab from "./MyEnvironmentsTab";
import { useMyEnvironments } from "src/api/simulate-environments/environments";
import { DEFAULT_PAGE_SIZE } from "./myEnvironments.constants";
import PersonasTab from "./PersonasTab";
import BuildEnvironmentDrawer from "./buildDrawer/BuildEnvironmentDrawer";
import { BUILD_PARAM } from "./buildDrawer/buildDraft";
import { ENTRY_TAB, ENVIRONMENTS_HEADER } from "./environmentOptions";

const TAB_SX = {
  minWidth: 0,
  px: 0.75,
  "&:not(:last-of-type)": { mr: 2.5 },
  color: "text.secondary",
  "&.Mui-selected": { color: "text.primary" },
};

const TAB_PANELS = {
  [ENTRY_TAB.MY]: MyEnvironmentsTab,
  [ENTRY_TAB.PERSONAS]: PersonasTab,
};

// "My Environments 37": how many there are in all, from the unfiltered first
// page. It shares the list's query while the list shows that page; on a
// filtered or later page, or on Personas, it is its own request and polls
// like the list while anything is building.
function MyEnvironmentsLabel() {
  const { data, isSuccess } = useMyEnvironments({
    page: 0,
    pageSize: DEFAULT_PAGE_SIZE,
  });
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} component="span">
      <span>My environments</span>
      {isSuccess && (
        <Box
          component="span"
          sx={{
            typography: "s2",
            fontVariantNumeric: "tabular-nums",
          }}
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
  const [params, setParams] = useSearchParams();
  const buildWay = params.get(BUILD_PARAM);

  const setBuildWay = useCallback(
    (next) =>
      setParams(
        (current) => {
          const q = new URLSearchParams(current);
          if (next === null) q.delete(BUILD_PARAM);
          else q.set(BUILD_PARAM, next);
          return q;
        },
        { replace: true },
      ),
    [setParams],
  );

  // A link to the old Build tab opens the drawer over My Environments.
  useEffect(() => {
    if (!opensBuild) return;
    setParams(
      (current) => {
        const q = new URLSearchParams(current);
        q.set("tab", ENTRY_TAB.MY);
        q.set(BUILD_PARAM, q.get(BUILD_PARAM) || "");
        return q;
      },
      { replace: true },
    );
  }, [opensBuild, setParams]);

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
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      <PageHeader
        sx={{ px: 3, pt: 2, mb: 1.5, flexShrink: 0 }}
        title={ENVIRONMENTS_HEADER.title}
        description={
          tab === ENTRY_TAB.PERSONAS
            ? ENVIRONMENTS_HEADER.personasSubtitle
            : ENVIRONMENTS_HEADER.subtitle
        }
        actions={
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
            onClick={() => setBuildWay("")}
          >
            Build environment
          </Button>
        }
      />

      <Box
        sx={{
          px: 3,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Tabs
          sx={{ boxShadow: "none" }}
          value={tab}
          onChange={(_, v) => onTabChange(v)}
        >
          <Tab
            value={ENTRY_TAB.MY}
            disableRipple
            label={<MyEnvironmentsLabel />}
            sx={TAB_SX}
          />
          <Tab
            value={ENTRY_TAB.PERSONAS}
            disableRipple
            label="Personas"
            sx={TAB_SX}
          />
        </Tabs>
      </Box>

      <TabPanel onBuild={() => setBuildWay("")} />
      <BuildEnvironmentDrawer
        open={buildWay !== null}
        way={buildWay}
        onWayChange={setBuildWay}
        onClose={() => setBuildWay(null)}
      />
    </Box>
  );
}
