import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PageHeader from "src/components/page-header";
import Iconify from "src/components/iconify";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import { useDebounce } from "src/hooks/use-debounce";
import { ROLES } from "src/utils/rolePermissionMapping";
import { useMetricsList } from "./api";
import { metricWindow } from "./metricTime";
import useCoverageReanchor from "./useCoverageReanchor";
import MetricList from "./MetricList";
import MetricProjectSelect from "./MetricProjectSelect";

const DESCRIPTION = "Track outcomes across conversations, spans and users.";
const WINDOWS = [
  [1, "Last 24 hours"],
  [7, "Last 7 days"],
  [30, "Last 30 days"],
];
// Toolbar controls share the search field's 30px height so the row aligns.
const CONTROL_SX = { "& .MuiOutlinedInput-root": { height: 30 } };

// A workspace change remounts all local filters/cursors before any new read.
export default function MetricsListPage() {
  const {
    currentOrganizationId,
    currentOrganizationRole,
    isReady: organizationReady,
  } = useOrganization();
  const {
    currentWorkspaceId,
    currentWorkspaceRole,
    isReady: workspaceReady,
  } = useWorkspace();
  if (!organizationReady || !workspaceReady) {
    return (
      <Box sx={{ p: 2 }}>
        <PageHeader title="Metrics" description={DESCRIPTION} />
        <Typography role="status" variant="body2" color="text.secondary">
          Loading workspace…
        </Typography>
      </Box>
    );
  }
  if (!currentOrganizationId || !currentWorkspaceId) {
    return <Alert severity="info">Select a workspace to view metrics.</Alert>;
  }
  return (
    <WorkspaceMetricsList
      key={`${currentOrganizationId}:${currentWorkspaceId}`}
      scope={{
        organizationId: currentOrganizationId,
        workspaceId: currentWorkspaceId,
      }}
      canCreate={
        [ROLES.OWNER, ROLES.ADMIN].includes(currentOrganizationRole) ||
        [ROLES.WORKSPACE_ADMIN, ROLES.WORKSPACE_MEMBER].includes(
          currentWorkspaceRole,
        )
      }
    />
  );
}

function WorkspaceMetricsList({ scope, canCreate }) {
  const router = useRouter();
  const [projectId, setProjectId] = useState("");
  const [projectSearch, setProjectSearch] = useState("");
  const [projectPage, setProjectPage] = useState(0);
  const settledProjectSearch = useDebounce(projectSearch, 300);
  const [pagination, setPagination] = useState({
    page: 0,
    pageSize: 25,
    cursors: [null],
  });
  const [days, setDays] = useState(7);
  const [window, setWindow] = useState(() => metricWindow(7));
  const windowLabel = WINDOWS.find(([value]) => value === days)?.[1];
  const selectedScope = { ...scope, projectId: projectId || undefined };
  const definitions = useMetricsList({
    scope: selectedScope,
    params: {
      limit: pagination.pageSize,
      ...(pagination.cursors[pagination.page] && {
        before: pagination.cursors[pagination.page],
      }),
    },
  });
  // A preset list window moves forward when a forward-only metric's first
  // whole bucket ends, so its read begins without requiring a page reload.
  useCoverageReanchor(
    window,
    definitions.data?.results?.map((metric) => metric.definition) || [],
    () => setWindow(metricWindow(days)),
  );
  const projects = useQuery({
    queryKey: [
      "metrics-project-picker",
      scope,
      settledProjectSearch,
      projectPage,
    ],
    queryFn: ({ signal }) =>
      readObserveProjectPage({
        signal,
        params: {
          page_number: projectPage,
          page_size: 50,
          name: settledProjectSearch,
        },
      }),
    retry: false,
    placeholderData: undefined,
    meta: { errorHandled: true },
  });
  const resetPage = () =>
    setPagination((previous) => ({ ...previous, page: 0, cursors: [null] }));
  const createAction = (
    <Tooltip
      title={
        canCreate
          ? ""
          : "A workspace member role is required to create metrics."
      }
    >
      <span>
        <Button
          variant="contained"
          color="inherit"
          size="small"
          disabled={!canCreate}
          startIcon={<Iconify icon="solar:add-linear" width={16} />}
          onClick={() => router.push(paths.dashboard.metrics.new)}
        >
          Create metric
        </Button>
      </span>
    </Tooltip>
  );
  return (
    <Stack
      sx={{ p: 2, height: "100%", minHeight: 0, bgcolor: "background.paper" }}
    >
      <PageHeader title="Metrics" description={DESCRIPTION} sx={{ mb: 0 }} />
      {projects.isError && (
        <Alert
          severity="warning"
          sx={{ mt: 1.5 }}
          action={
            <Button color="inherit" onClick={() => projects.refetch()}>
              Retry projects
            </Button>
          }
        >
          Project choices could not be loaded. The metrics list is still
          available.
        </Alert>
      )}
      <MetricList
        key={projectId || "all-projects"}
        definitions={definitions}
        scope={selectedScope}
        window={window}
        windowLabel={windowLabel}
        onSelect={(metric) =>
          router.push(paths.dashboard.metrics.detail(metric.id))
        }
        pagination={pagination}
        onPageChange={(page, cursor) =>
          setPagination((previous) => ({
            ...previous,
            page,
            cursors:
              page > previous.page
                ? [...previous.cursors.slice(0, page), cursor]
                : previous.cursors,
          }))
        }
        onPageSizeChange={(pageSize) =>
          setPagination({ page: 0, pageSize, cursors: [null] })
        }
        createAction={createAction}
        toolbar={
          <>
            <MetricProjectSelect
              value={projectId}
              projects={projects}
              search={settledProjectSearch}
              page={projectPage}
              label="Project filter"
              placeholder="All projects"
              hideLabel
              sx={{ width: 220, ...CONTROL_SX }}
              onSearch={(text) => {
                setProjectSearch(text);
                setProjectPage(0);
              }}
              onPage={setProjectPage}
              onChange={(id) => {
                setProjectId(id);
                resetPage();
              }}
            />
            <TextField
              select
              size="small"
              value={days}
              inputProps={{ "aria-label": "Value window" }}
              sx={{ width: 150, ...CONTROL_SX }}
              onChange={(event) => {
                const next = Number(event.target.value);
                setDays(next);
                setWindow(metricWindow(next));
              }}
            >
              {WINDOWS.map(([value, label]) => (
                <MenuItem key={value} value={value}>
                  {label}
                </MenuItem>
              ))}
            </TextField>
          </>
        }
        actions={
          <Tooltip title="Read values again for a fresh window ending now">
            <span>
              <Button
                variant="outlined"
                color="inherit"
                size="small"
                aria-label="Refresh values"
                disabled={definitions.readState === "loading"}
                startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
                onClick={() => {
                  setWindow(metricWindow(days));
                  void definitions.refetch();
                }}
              >
                Refresh
              </Button>
            </span>
          </Tooltip>
        }
      />
    </Stack>
  );
}
WorkspaceMetricsList.propTypes = {
  scope: PropTypes.object.isRequired,
  canCreate: PropTypes.bool.isRequired,
};
