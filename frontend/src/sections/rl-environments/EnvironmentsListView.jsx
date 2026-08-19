import { Box, Button, Stack, Typography } from "@mui/material";
import { formatDistanceToNow } from "date-fns";
import PropTypes from "prop-types";
import { useCallback, useMemo, useState } from "react";

import { DataTable, DataTablePagination } from "src/components/data-table";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";


const environmentName = (env) =>
  env?.agent || env?.title || env?.session_id || "-";

function NameCell({ row }) {
  return (
    <Typography
      variant="body2"
      noWrap
      sx={{ fontWeight: 500, color: "text.primary" }}
    >
      {environmentName(row.original)}
    </Typography>
  );
}

function DescriptionCell({ getValue }) {
  const value = getValue();
  if (!value) {
    return (
      <Typography variant="body2" sx={{ fontSize: 13, color: "text.disabled" }}>
        -
      </Typography>
    );
  }
  // The cell clips with an ellipsis; the untruncated text stays reachable as a
  // native tooltip so nothing is lost from the list.
  return (
    <Typography
      variant="body2"
      noWrap
      title={value}
      sx={{
        fontSize: 13,
        color: "text.secondary",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
    >
      {value}
    </Typography>
  );
}

function CountCell({ getValue }) {
  const value = getValue();
  return (
    <Typography variant="body2" sx={{ fontSize: 13, color: "text.primary" }}>
      {Number.isFinite(value) ? value : 0}
    </Typography>
  );
}

function RunsCell({ row }) {
  const { runs = 0, runs_passed: runsPassed = 0 } = row.original ?? {};
  // Neutral until it has been run; green when everything passed; red only when nothing did.
  // A partially-passing environment has not failed, so it does not wear the failure colour.
  const tone =
    runs === 0
      ? "neutral"
      : runsPassed >= runs
        ? "pass"
        : runsPassed === 0
          ? "fail"
          : "partial";
  // accent carries a value per theme; the .main ramp is dark-tuned and meant for fills.
  const TONES = { pass: "accent.pass", fail: "accent.fail", partial: "accent.tool" };
  const color = TONES[tone] || "text.secondary";
  const borderColor = TONES[tone] || "divider";

  return (
    <Box
      data-testid="runs-chip"
      data-tone={tone}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        border: "1px solid",
        borderColor,
        borderRadius: 0.5,
        px: 1,
        py: 0.25,
      }}
    >
      <Typography variant="caption" sx={{ fontWeight: 500, color }}>
        {runs === 0 ? "No runs" : `${runsPassed}/${runs}`}
      </Typography>
    </Box>
  );
}

function UpdatedCell({ getValue }) {
  const value = getValue();
  // The contract sends epoch SECONDS as a float — Date wants milliseconds.
  const date = Number.isFinite(value) ? new Date(value * 1000) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return (
      <Typography variant="body2" sx={{ fontSize: 13, color: "text.disabled" }}>
        -
      </Typography>
    );
  }
  return (
    <Typography variant="body2" sx={{ fontSize: 13, color: "text.secondary" }}>
      {formatDistanceToNow(date, { addSuffix: true })}
    </Typography>
  );
}

function EmptyState({ title, description, action }) {
  return (
    <Box
      sx={{
        // No panel — a border around this read as a stray band. The content is centred in
        // whatever height is left instead of pinned to the top, which left it clinging to the
        // header above a large void.
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1,
        textAlign: "center",
        maxWidth: 460,
        mx: "auto",
        pb: 6,
      }}
    >
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        {title}
      </Typography>
      <Typography
        variant="body2"
        sx={{ color: "text.secondary", maxWidth: 420 }}
      >
        {description}
      </Typography>
      {action}
    </Box>
  );
}

const EnvironmentsListView = ({
  environments = [],
  isLoading = false,
  onAdd,
  onOpen,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  // The contract exposes no query params and no pagination, so both search and
  // paging run against the full list in memory.
  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return environments;
    return environments.filter((env) => {
      const haystack = `${environmentName(env)} ${env?.one_liner ?? ""}`;
      return haystack.toLowerCase().includes(query);
    });
  }, [environments, searchQuery]);

  const pageRows = useMemo(
    () => filtered.slice(page * pageSize, page * pageSize + pageSize),
    [filtered, page, pageSize],
  );

  const handleRowClick = useCallback(
    (row) => {
      if (!row?.session_id) return;
      onOpen?.(row.session_id);
    },
    [onOpen],
  );

  const columns = useMemo(
    () => [
      {
        id: "name",
        accessorKey: "agent",
        header: "Name",
        meta: { flex: 1.4 },
        minSize: 200,
        enableSorting: false,
        cell: NameCell,
      },
      {
        id: "one_liner",
        accessorKey: "one_liner",
        header: "Description",
        meta: { flex: 2 },
        minSize: 260,
        enableSorting: false,
        cell: DescriptionCell,
      },
      {
        id: "tools",
        accessorKey: "tools",
        header: "Tools",
        size: 90,
        enableSorting: false,
        cell: CountCell,
      },
      {
        id: "sub_goals",
        accessorKey: "sub_goals",
        header: "Sub-goals",
        size: 110,
        enableSorting: false,
        cell: CountCell,
      },
      {
        id: "scenarios",
        accessorKey: "scenarios",
        header: "Scenarios",
        size: 110,
        enableSorting: false,
        cell: CountCell,
      },
      {
        id: "runs",
        accessorKey: "runs",
        header: "Runs",
        size: 120,
        enableSorting: false,
        cell: RunsCell,
      },
      {
        id: "updated",
        accessorKey: "updated",
        header: "Updated",
        size: 160,
        enableSorting: false,
        cell: UpdatedCell,
      },
    ],
    [],
  );

  const hasEnvironments = environments.length > 0;
  const showNoEnvironments = !isLoading && !hasEnvironments;
  const showNoMatches = !isLoading && hasEnvironments && filtered.length === 0;

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 2,
        }}
      >
        <Stack spacing={0.25}>
          <Typography
            color="text.primary"
            typography="m2"
            fontWeight="fontWeightSemiBold"
          >
            RL Environments
          </Typography>
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight="fontWeightRegular"
          >
            Reinforcement learning environments built from your agent
            transcripts — tools, sub-goals and the scenarios they are graded on
          </Typography>
        </Stack>
        {/* The empty state carries its own call to action, so a second one up here is just
            the same button twice. */}
        {hasEnvironments && (
          <Button
            variant="contained"
            color="primary"
            sx={{ px: 3, borderRadius: "4px", height: 38 }}
            startIcon={
              <Iconify icon="octicon:plus-24" sx={{ width: 20, height: 20 }} />
            }
            onClick={() => onAdd?.()}
          >
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Add Environment
            </Typography>
          </Button>
        )}
      </Box>

      {hasEnvironments && (
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <FormSearchField
            size="small"
            placeholder="Search"
            inputProps={{ "aria-label": "Search environments" }}
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
            searchQuery={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setPage(0);
            }}
          />
        </Box>
      )}

      {showNoEnvironments && (
        <EmptyState
          title="No environments yet"
          description="Create your first RL environment to turn an agent transcript into tools, sub-goals and scenarios you can run against."
          action={
            <Button
              variant="contained"
              color="primary"
              sx={{ mt: 1, px: 3, borderRadius: "4px", height: 38 }}
              startIcon={
                <Iconify
                  icon="octicon:plus-24"
                  sx={{ width: 20, height: 20 }}
                />
              }
              onClick={() => onAdd?.()}
            >
              <Typography typography="s1" fontWeight="fontWeightMedium">
                Create your first environment
              </Typography>
            </Button>
          }
        />
      )}

      {showNoMatches && (
        <EmptyState
          title="No environments match your search"
          description={`Nothing matched "${searchQuery.trim()}". Try a different name or description.`}
        />
      )}

      {!showNoEnvironments && !showNoMatches && (
        <>
          <DataTable
            columns={columns}
            data={pageRows}
            isLoading={isLoading}
            rowCount={filtered.length}
            onRowClick={handleRowClick}
            getRowId={(row) => row.session_id}
            rowHeight={44}
            emptyMessage="No environments found"
          />

          <DataTablePagination
            page={page}
            pageSize={pageSize}
            total={filtered.length}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(0);
            }}
          />
        </>
      )}
    </Box>
  );
};

const cellPropTypes = {
  getValue: PropTypes.func,
  row: PropTypes.shape({ original: PropTypes.object }),
};

NameCell.propTypes = cellPropTypes;
DescriptionCell.propTypes = cellPropTypes;
CountCell.propTypes = cellPropTypes;
RunsCell.propTypes = cellPropTypes;
UpdatedCell.propTypes = cellPropTypes;

EmptyState.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  action: PropTypes.node,
};

EnvironmentsListView.propTypes = {
  environments: PropTypes.arrayOf(PropTypes.object),
  isLoading: PropTypes.bool,
  onAdd: PropTypes.func,
  onOpen: PropTypes.func,
};

export default EnvironmentsListView;
