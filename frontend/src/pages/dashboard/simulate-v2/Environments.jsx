import { useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";
import { Box, Tab, Tabs } from "@mui/material";
import { paths } from "src/routes/paths";
import { useSimStore } from "src/sections/simulate-v2/store";
import StartEnvironment from "src/sections/simulate-v2/environments/StartEnvironment";
import MyEnvironmentsTable from "src/sections/simulate-v2/environments/MyEnvironmentsTable";

/**
 * Environments page shell.
 *
 * Two tabs at the top:
 *   • Build environment — the picker (templates / clones / connect-your-
 *     agent / scratch). Renders StartEnvironment.
 *   • My Environments  — the table of envs already built, matching the
 *     list on the Simulated Runs page. Clicking a row opens the
 *     environment workspace.
 *
 * The Simulated Runs page keeps its own env list for the runs-oriented
 * story; this tab exists so users landing on Environments can also see
 * what they've already built without leaving the create/list surface.
 */
const TABS = [
  { key: "build", label: "Build environment" },
  { key: "my", label: "My Environments" },
];

export default function EnvironmentsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState("build");
  const { state } = useSimStore();
  const existing = state?.myEnvironments || [];

  return (
    <>
      <Helmet>
        <title>Environments | Future AGI</title>
      </Helmet>

      <Box
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{
              px: 2,
              minHeight: 42,
              "& .MuiTabs-indicator": { backgroundColor: "#7857FC" },
            }}
          >
            {TABS.map((t) => (
              <Tab
                key={t.key}
                value={t.key}
                disableRipple
                label={t.label}
                sx={{
                  minHeight: 42,
                  px: 1.5,
                  typography: "s2",
                  fontWeight: 700,
                  textTransform: "none",
                  color: "text.secondary",
                  "&.Mui-selected": { color: "text.primary" },
                }}
              />
            ))}
          </Tabs>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {tab === "build" && <StartEnvironment entry />}
          {tab === "my" && (
            <Box sx={{ p: 2, height: "100%", display: "flex", flexDirection: "column", gap: 1.5, minHeight: 0 }}>
              <MyEnvironmentsTable
                envs={existing}
                onOpen={(env) =>
                  navigate(paths.dashboard.simulate.environmentDetail(env.id))
                }
              />
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}
