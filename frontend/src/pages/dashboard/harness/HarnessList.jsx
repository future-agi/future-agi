import { useMemo, useState } from "react";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Helmet } from "react-helmet-async";
import { useNavigate } from "react-router-dom";

import { DataTable } from "src/components/data-table";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import CustomTooltip from "src/components/tooltip";
import SvgColor from "src/components/svg-color";
import { useDebounce } from "src/hooks/use-debounce";
import { listHarnessJobs } from "src/api/harness/harness";
import { paths } from "src/routes/paths";

import {
  ICON_GUTTER,
  ICON_SIZE,
  agentTypeIcon,
  errorMessage,
  readable,
  stageStatus,
  environmentName,
} from "./harnessShared";

const updatedLabel = (status) => {
  if (!status?.updated_at) return null;
  const updatedAt = new Date(status.updated_at);
  if (Number.isNaN(updatedAt.getTime())) return null;
  return {
    relative: formatDistanceToNow(updatedAt, { addSuffix: true }),
    absolute: updatedAt.toLocaleString(),
  };
};

export default function HarnessList() {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery, 300);

  const { isPending, data, error } = useQuery({
    queryKey: ["harness-jobs"],
    queryFn: listHarnessJobs,
    // Keeps the status chips of in-flight runs current while the list is open. The pages
    // render their own error state, so the global toast handler stays out of the way.
    refetchInterval: 5000,
    meta: { errorHandled: true },
  });

  const jobs = useMemo(() => (Array.isArray(data) ? data : []), [data]);

  const filtered = useMemo(() => {
    const term = debouncedSearchQuery.trim().toLowerCase();
    if (!term) return jobs;
    return jobs.filter((item) =>
      [environmentName(item.job, ""), item.job?.run_id]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term)),
    );
  }, [jobs, debouncedSearchQuery]);

  const goToDetail = (jobId) =>
    navigate(paths.dashboard.simulate.harness.detail(jobId));
  const goToCreate = () => navigate(paths.dashboard.simulate.harness.new);

  const columns = useMemo(
    () => [
      {
        id: "name",
        header: "Name",
        meta: { flex: 1.4 },
        minSize: 200,
        enableSorting: false,
        cell: ({ row }) => (
          <Typography variant="body2" fontWeight={500} noWrap>
            {environmentName(row.original.job)}
          </Typography>
        ),
      },
      {
        id: "type",
        header: "Type",
        size: 180,
        enableSorting: false,
        // The glyph is what the label says, so the two belong together.
        cell: ({ row }) => {
          const typeIcon = agentTypeIcon(row.original);
          return (
            <Stack
              direction="row"
              alignItems="center"
              sx={{ minWidth: 0, gap: `${ICON_GUTTER}px` }}
            >
              <SvgColor
                src={typeIcon.src}
                sx={{
                  width: ICON_SIZE,
                  height: ICON_SIZE,
                  flexShrink: 0,
                  color: "text.secondary",
                }}
              />
              <Typography variant="body2" color="text.secondary" noWrap>
                {typeIcon.label}
              </Typography>
            </Stack>
          );
        },
      },
      {
        id: "status",
        header: "Status",
        size: 190,
        enableSorting: false,
        cell: ({ row }) => (
          <StatusChip
            label={readable(row.original.status?.stage)}
            status={stageStatus(row.original.status?.stage)}
            showIcon={false}
          />
        ),
      },
      {
        id: "run",
        header: "Run ID",
        meta: { flex: 1 },
        minSize: 180,
        enableSorting: false,
        cell: ({ row }) => (
          <Typography variant="body2" color="text.secondary" noWrap>
            {row.original.job?.run_id || "—"}
          </Typography>
        ),
      },
      {
        id: "updated",
        header: "Updated",
        size: 170,
        enableSorting: false,
        cell: ({ row }) => {
          const label = updatedLabel(row.original.status);
          if (!label)
            return (
              <Typography variant="body2" color="text.secondary">
                —
              </Typography>
            );
          return (
            <CustomTooltip
              show
              arrow
              size="small"
              title={label.absolute}
              placement="top"
            >
              <Typography variant="body2" color="text.secondary" noWrap>
                {label.relative}
              </Typography>
            </CustomTooltip>
          );
        },
      },
    ],
    [],
  );

  const showEmptyScreen =
    !isPending && !error && jobs.length === 0 && !debouncedSearchQuery.trim();

  const createButton = (
    <Button
      variant="contained"
      startIcon={<Iconify icon="eva:plus-fill" />}
      onClick={goToCreate}
      sx={{
        bgcolor: "primary.main",
        color: "primary.contrastText",
        "&:hover": { bgcolor: "primary.dark" },
      }}
    >
      Create RL environment
    </Button>
  );

  return (
    <>
      <Helmet>
        <title>RL Environment | Future AGI</title>
      </Helmet>

      <Box sx={{ height: "100vh", p: 2 }}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            gap: 1.5,
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          <Box>
            <Typography typography="m2" fontWeight={600}>
              RL Environments
            </Typography>
            <Typography typography="s1" color="text.secondary">
              Create an environment where you test your agent. Hand over a
              folder and ALK builds the world, the data and the scenarios, then
              runs the calls and grades them.
            </Typography>
          </Box>

          {error && (
            <Alert severity="error" variant="outlined">
              {errorMessage(error)}
            </Alert>
          )}

          {showEmptyScreen ? (
            <EmptyLayout
              title="No RL environments yet"
              description="Give us the agent folder; ALK does the rest."
              action={createButton}
              icon="/assets/icons/ic_bot.svg"
            />
          ) : (
            <>
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 2,
                }}
              >
                <FormSearchField
                  searchQuery={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search environments"
                  sx={{
                    minWidth: "250px",
                    "& .MuiOutlinedInput-root": { height: "30px" },
                  }}
                />
                {createButton}
              </Box>

              <DataTable
                columns={columns}
                data={filtered}
                isLoading={isPending}
                rowCount={filtered.length}
                onRowClick={(row) => goToDetail(row.job.job_id)}
                getRowId={(row) => row.job.job_id}
                rowHeight={44}
                emptyMessage="No RL environments found"
              />
            </>
          )}
        </Box>
      </Box>
    </>
  );
}
