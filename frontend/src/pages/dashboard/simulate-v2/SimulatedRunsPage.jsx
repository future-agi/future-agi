import { Helmet } from "react-helmet-async";
import { Box, Button } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { paths } from "src/routes/paths";
import MyEnvironmentsTable from "src/sections/simulate-v2/environments/MyEnvironmentsTable";
import { SimStoreProvider, useSimStore } from "src/sections/simulate-v2/store";

/**
 * Simulated Runs (new).
 *
 * The nav item "Simulated Runs" used to land on the legacy RunTests
 * screen. In the picker-first world the Environments page owns
 * creation and this page owns *what's already been created* — the
 * same data the old "My Environments" tab in the picker used to show,
 * rendered as the primary view.
 *
 * Wraps its own SimStoreProvider because /simulate/test lives outside
 * the pathless SimStoreProvider layout the /environments routes share.
 * The store hydrates from localStorage on mount, so envs created via
 * the picker land here correctly on the next visit.
 *
 * Legacy RunTests is preserved at /simulate/test/legacy so the old
 * flow is one URL away — nothing was deleted, just reordered.
 */
export default function SimulatedRunsPage() {
  return (
    <SimStoreProvider>
      <Helmet>
        <title>Simulated Runs | Future AGI</title>
      </Helmet>
      <SimulatedRunsInner />
    </SimStoreProvider>
  );
}

function SimulatedRunsInner() {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const existing = state?.myEnvironments || [];

  return (
    <Box
      sx={{
        /* Datasets-page pattern: full-height flex column, table
           flex-fills the middle, pagination pins to the bottom
           edge of the viewport rather than to the last row. */
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
        p: 2,
      }}
    >
      <PageHeader
        title="Simulated runs"
        description="Every environment you've created, its readiness, and how its scenarios and runs are tracking. Click a row to open the environment workspace."
        sx={{ mb: 0, flexShrink: 0 }}
        actions={
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => navigate(paths.dashboard.simulate.environments)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          >
            Create a simulation
          </Button>
        }
      />
      <MyEnvironmentsTable
        envs={existing}
        onOpen={(env) =>
          navigate(paths.dashboard.simulate.environmentDetail(env.id))
        }
        hideStatus
      />
    </Box>
  );
}
