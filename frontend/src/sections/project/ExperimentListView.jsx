import { Box, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import React, { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import PropTypes from "prop-types";
import { formatNumberWithCommas } from "../projects/UsersView/common";

const SORT_FIELD_MAP = {
  name: "name",
  createdAt: "created_at",
  updatedAt: "updated_at",
};

const ExperimentListView = React.forwardRef(
  ({ searchQuery, setSelectedRowsData }, ref) => {
    const navigate = useNavigate();

    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState(25);
    const [sorting, setSorting] = useState([]);
    const [rowSelection, setRowSelection] = useState({});

    const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

    const sortBy = sorting[0]
      ? SORT_FIELD_MAP[sorting[0].id] || undefined
      : undefined;
    const sortOrder = sorting[0]?.desc
      ? "desc"
      : sorting[0]
        ? "asc"
        : undefined;

    const { data, isLoading } = useQuery({
      queryKey: [
        "experiment-projects",
        { page, pageSize, search: debouncedSearchQuery, sortBy, sortOrder },
      ],
      queryFn: async () => {
        const { data } = await axios.get(
          endpoints.project.projectExperimentList,
          {
            params: {
              name: debouncedSearchQuery?.length ? debouncedSearchQuery : null,
              page_number: page,
              page_size: pageSize,
              sort_by: sortBy,
              sort_direction: sortOrder,
              project_type: "experiment",
            },
          },
        );
        return data?.result;
      },
    });

    const items = data?.projects || [];
    const total = data?.total_count || 0;

    // Expose clearSelection for the parent
    React.useImperativeHandle(ref, () => ({
      clearSelection: () => setRowSelection({}),
    }));

    // Sync selection → parent's selectedRowsData (array of IDs)
    const handleRowSelectionChange = useCallback(
      (newSelection) => {
        setRowSelection(newSelection);
        const ids = Object.keys(newSelection)
          .filter((k) => newSelection[k])
          .map((k) => items[parseInt(k, 10)]?.id)
          .filter(Boolean);
        setSelectedRowsData(ids);
      },
      [items, setSelectedRowsData],
    );

    const handleSortingChange = useCallback((newSorting) => {
      setSorting(newSorting);
      if (newSorting.length > 0) {
        trackEvent(Events.projectSort, {
          [PropertyName.click]: newSorting,
        });
      }
    }, []);

    const columns = useMemo(
      () => [
        {
          id: "name",
          accessorKey: "name",
          header: "Project Name",
          meta: { flex: 1.5 },
          minSize: 200,
          cell: ({ getValue }) => (
            <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
              {getValue()}
            </Typography>
          ),
        },
        {
          id: "traceCount",
          accessorKey: "trace_count",
          header: "No. of Datapoints",
          size: 160,
          enableSorting: false,
          cell: ({ getValue }) => (
            <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
              {formatNumberWithCommas(getValue())}
            </Typography>
          ),
        },
        {
          id: "runCount",
          accessorKey: "run_count",
          header: "No. of Runs",
          size: 160,
          enableSorting: false,
          cell: ({ getValue }) => (
            <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
              {formatNumberWithCommas(getValue() ?? 0)}
            </Typography>
          ),
        },
        {
          id: "createdAt",
          accessorKey: "created_at",
          header: "Date Created",
          size: 150,
          cell: ({ getValue }) => {
            const val = getValue();
            if (!val) return null;
            try {
              return (
                <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
                  {formatDistanceToNow(new Date(val), { addSuffix: true })}
                </Typography>
              );
            } catch {
              return null;
            }
          },
        },
        {
          id: "updatedAt",
          accessorKey: "updated_at",
          header: "Date Modified",
          size: 150,
          cell: ({ getValue }) => {
            const val = getValue();
            if (!val) return null;
            try {
              return (
                <Typography variant="body2" noWrap sx={{ fontSize: "12px" }}>
                  {formatDistanceToNow(new Date(val), { addSuffix: true })}
                </Typography>
              );
            } catch {
              return null;
            }
          },
        },
      ],
      [],
    );

    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          gap: 1,
        }}
      >
        <DataTable
          columns={columns}
          data={items}
          isLoading={isLoading}
          rowCount={total}
          sorting={sorting}
          onSortingChange={handleSortingChange}
          rowSelection={rowSelection}
          onRowSelectionChange={handleRowSelectionChange}
          onRowClick={(row) => {
            navigate(`/dashboard/prototype/${row.id}`, {
              state: { dataset: row },
            });
            trackEvent(Events.projectSelected, {
              [PropertyName.click]: {
                "Project Name": row.name,
                "Project Id": row.id,
              },
            });
          }}
          getRowId={(row) => row.id}
          enableSelection
          emptyMessage="No projects found"
        />
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
      </Box>
    );
  },
);

ExperimentListView.displayName = "ExperimentListView";

ExperimentListView.propTypes = {
  searchQuery: PropTypes.string,
  setSelectedRowsData: PropTypes.func,
};

ExperimentListView.defaultProps = {
  searchQuery: "",
};

export default ExperimentListView;
