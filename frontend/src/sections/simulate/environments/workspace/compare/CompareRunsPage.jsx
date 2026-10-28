import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { LoadingScreen } from "src/components/loading-screen";
import { paths } from "src/routes/paths";
import { pageBackButtonSx } from "src/sections/settings/integrations/styles";
import { useEnvironment } from "src/api/simulate-environments/environment";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";

import { useEnvState } from "../../store/envState";
import { BUILD_STATUS } from "../../myEnvironments.constants";
import EmptyState from "../../components/EmptyState";
import WorkspaceErrorBoundary from "../WorkspaceErrorBoundary";
import RunPills from "./RunPills";
import AddRunMenu from "./AddRunMenu";
import { comparisonPayload, downloadComparison } from "./exportComparison";
import { coverageFromRuns, subtitleText, withIdentity } from "./compareFormat";

// The most runs the page reads for its pills and the Add run menu.
const RUN_CAP = 200;

const ICON_SX = { color: "text.subtitle" };

/**
 * Two or more runs of one environment, side by side.
 *
 * `?runs=a,b` names them; the first is the baseline and everything else is
 * read against it. The header says what is being compared and how far the
 * runs are the same measurement; the body reads them scenario by scenario.
 */
function CompareRuns() {
  const { envId } = useParams();
  const navigate = useNavigate();
  const from = useLocation().state?.back || null;
  const [params, setParams] = useSearchParams();
  const [addAnchor, setAddAnchor] = useState(null);

  const { env, bootstrapState, notFound, error, refetch, isLoading } =
    useEnvironment(envId);
  // Seed the store only from a finished build, as the workspace does.
  const building =
    env?.buildStatus === BUILD_STATUS.BUILDING ||
    env?.buildStatus === BUILD_STATUS.FAILED;
  const { envState } = useEnvState(
    envId,
    building ? undefined : bootstrapState,
  );
  const {
    runs: envRuns,
    count,
    isLoading: runsLoading,
  } = useEnvironmentRuns(env, envState, { pageSize: RUN_CAP });

  const runIds = useMemo(
    () => (params.get("runs") || "").split(",").filter(Boolean),
    [params],
  );
  const byId = useMemo(
    () => new Map(envRuns.map((r) => [r.id, withIdentity(r)])),
    [envRuns],
  );
  const runs = useMemo(
    () => runIds.map((id) => byId.get(id)).filter(Boolean),
    [runIds, byId],
  );
  const others = useMemo(
    () => envRuns.filter((r) => !runIds.includes(r.id)).map(withIdentity),
    [envRuns, runIds],
  );

  const back = () =>
    from?.to
      ? navigate(from.to, { state: from.state })
      : navigate(
          paths.dashboard.simulate.environments.workspaceTab(envId, "runs"),
        );

  const setRuns = (ids) => {
    const next = new URLSearchParams(params);
    next.set("runs", ids.join(","));
    setParams(next, { replace: true });
  };

  const exportComparison = () =>
    downloadComparison(comparisonPayload({ env, runs }));

  const copyLink = () => {
    navigator.clipboard?.writeText(window.location.href);
    enqueueSnackbar("Link copied", { variant: "success" });
  };

  if (isLoading || (env && runsLoading)) return <LoadingScreen />;

  const backButton = (
    <Button
      variant="outlined"
      size="small"
      onClick={back}
      sx={pageBackButtonSx}
    >
      {from?.label ? `Back to ${from.label}` : "Back to runs"}
    </Button>
  );

  if (!env) {
    return (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title={
          notFound ? "Environment not found" : "Couldn't load this environment"
        }
        body={notFound ? null : error?.message}
        action={
          notFound ? (
            backButton
          ) : (
            <Button variant="outlined" size="small" onClick={() => refetch?.()}>
              Retry
            </Button>
          )
        }
      />
    );
  }

  if (runs.length < 2) {
    // Say what is missing; never draw a comparison of runs that don't exist.
    const finished = envRuns.filter((r) => r.finishedAt).length;
    return (
      <EmptyState
        icon="solar:transfer-horizontal-linear"
        title={finished < 2 ? "Not yet run" : "Nothing to compare"}
        body={
          finished < 2
            ? `Comparing needs two finished runs. This environment has ${finished}. Run it, then compare.`
            : "Pick two or more runs on the Runs tab and press Compare."
        }
        action={backButton}
      />
    );
  }

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2, pt: 1.5, pb: 1 }}
        >
          {backButton}
          <Box
            sx={{ height: 20, width: "1px", bgcolor: "divider", flexShrink: 0 }}
          />
          <Box minWidth={0}>
            <Typography
              noWrap
              sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
            >
              {env.name}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {subtitleText(runs, coverageFromRuns(runs))}
            </Typography>
          </Box>
          <Box flex={1} />
          <CustomTooltip show arrow size="small" title="Export comparison">
            <IconButton
              size="small"
              aria-label="Export comparison"
              onClick={exportComparison}
            >
              <Iconify
                icon="solar:download-minimalistic-linear"
                width={16}
                sx={ICON_SX}
              />
            </IconButton>
          </CustomTooltip>
          <CustomTooltip show arrow size="small" title="Copy link">
            <IconButton size="small" aria-label="Copy link" onClick={copyLink}>
              <Iconify icon="solar:share-linear" width={16} sx={ICON_SX} />
            </IconButton>
          </CustomTooltip>
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          <RunPills
            runs={runs}
            onRemove={(id) => setRuns(runIds.filter((x) => x !== id))}
          />
          <Button
            size="small"
            onClick={(e) => setAddAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            sx={{
              color: "text.secondary",
              border: "1px dashed",
              borderColor: "divider",
            }}
          >
            Add run
          </Button>
        </Stack>
      </Box>

      <AddRunMenu
        anchorEl={addAnchor}
        onClose={() => setAddAnchor(null)}
        runs={others}
        capped={count > RUN_CAP}
        onAdd={(ids) => setRuns([...runIds, ...ids])}
      />
    </Stack>
  );
}

export default function CompareRunsPage() {
  return (
    <WorkspaceErrorBoundary>
      <CompareRuns />
    </WorkspaceErrorBoundary>
  );
}
