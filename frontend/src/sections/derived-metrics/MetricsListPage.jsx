import PropTypes from "prop-types";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { readObserveProjectPage } from "src/api/project/observe-project-list";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import { useDebounce } from "src/hooks/use-debounce";
import { ROLES } from "src/utils/rolePermissionMapping";
import { useMetricsList } from "./api";
import { formatMetricWindow, metricWindow } from "./metricTime";
import MetricList from "./MetricList";
import MetricProjectSelect from "./MetricProjectSelect";

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
        <Typography component="h1" variant="h6" fontWeight={600}>
          Metrics
        </Typography>
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
  const [window, setWindow] = useState(() => metricWindow(7));
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
  return (
    <Stack
      spacing={2}
      sx={{ p: 2, height: "100%", minHeight: 0, bgcolor: "background.paper" }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Box>
          <Typography component="h1" variant="h6" fontWeight={600}>
            Metrics
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Track outcomes across conversations, spans and users.
          </Typography>
        </Box>
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
              disabled={!canCreate}
              onClick={() => router.push(paths.dashboard.metrics.new)}
              sx={{
                bgcolor: "text.primary",
                color: "background.paper",
                "&:hover": { bgcolor: "text.secondary" },
              }}
            >
              Create metric
            </Button>
          </span>
        </Tooltip>
      </Stack>
      <MetricList
        key={projectId || "all-projects"}
        definitions={definitions}
        scope={selectedScope}
        window={window}
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
        toolbar={
          <>
            <Box sx={{ width: 240 }}>
              <MetricProjectSelect
                value={projectId}
                projects={projects}
                search={settledProjectSearch}
                page={projectPage}
                label="Project filter"
                placeholder="All projects"
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
            </Box>
            <Tooltip title={formatMetricWindow(window)}>
              <Typography variant="body2" color="text.secondary" tabIndex={0}>
                Last 7 days
              </Typography>
            </Tooltip>
            <Button
              size="small"
              color="inherit"
              disabled={definitions.readState === "loading"}
              onClick={() => {
                setWindow(metricWindow(7));
                void definitions.refetch();
              }}
            >
              Refresh values
            </Button>
          </>
        }
      />
      {projects.isError && (
        <Alert
          severity="warning"
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
    </Stack>
  );
}
WorkspaceMetricsList.propTypes = {
  scope: PropTypes.object.isRequired,
  canCreate: PropTypes.bool.isRequired,
};
