import { Box, Chip, Avatar, Typography } from "@mui/material";
import React, { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import ApiKeyBar from "./ApiKeyBar";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import Image from "src/components/image";
import CreateApiKey from "./CreateApiKey";
import ActionMenu from "./ActionMenu";
import SecretKeyRenderer from "./SecretKeyRenderer";
import stringAvatar from "src/utils/stringAvatar";

export default function DevKeysView() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [sorting, setSorting] = useState([{ id: "created_at", desc: true }]);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [openCreateApiKey, setOpenCreateApiKey] = useState(false);

  const sortBy = sorting[0]?.id || "created_at";
  const sortOrder = sorting[0]?.desc ? "desc" : "asc";

  const { data, isLoading } = useQuery({
    queryKey: [
      "dev-keys",
      page,
      pageSize,
      debouncedSearchQuery,
      sortBy,
      sortOrder,
    ],
    queryFn: () =>
      axios.get(endpoints.keys.getKeys, {
        params: {
          search: debouncedSearchQuery || null,
          current_page_index: page,
          page_size: pageSize,
          sort_field: sortBy,
          sort_order: sortOrder,
        },
      }),
    select: (d) => d.data,
    keepPreviousData: true,
  });

  const rows = useMemo(
    () => (data?.status ? data?.result?.table : []) ?? [],
    [data],
  );
  const total =
    data?.result?.metadata?.totalRows ??
    data?.result?.metadata?.total_rows ??
    0;
  const hasData = rows.length > 0 || !!debouncedSearchQuery || isLoading;

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["dev-keys"] });
  }, [queryClient]);

  const columns = useMemo(
    () => [
      {
        id: "key_name",
        accessorKey: "key_name",
        header: "Key Name",
        meta: { flex: 1 },
        cell: ({ getValue, row }) => (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              minWidth: 0,
              width: "100%",
            }}
          >
            <Typography
              variant="body2"
              noWrap
              sx={{ fontSize: 13, minWidth: 0, flex: "0 1 auto" }}
            >
              {getValue()}
            </Typography>
            {!row.original.enabled && (
              <Chip
                label="Disabled"
                sx={{
                  ml: 1,
                  flexShrink: 0,
                  height: 22,
                  fontSize: 11,
                  borderRadius: "4px",
                  color: "text.primary",
                  bgcolor: "background.neutral",
                }}
              />
            )}
          </Box>
        ),
      },
      {
        id: "api_key",
        accessorKey: "api_key",
        header: "API Key",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ getValue }) => <SecretKeyRenderer value={getValue()} />,
      },
      {
        id: "secret_key",
        accessorKey: "secret_key",
        header: "Secret Key",
        meta: { flex: 1 },
        enableSorting: false,
        cell: ({ getValue }) => <SecretKeyRenderer value={getValue()} />,
      },
      {
        id: "created_by",
        accessorKey: "created_by",
        header: "Created By",
        meta: { flex: 1 },
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val) return "-";
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              <Avatar
                variant="rounded"
                {...stringAvatar(val)}
                sx={{
                  width: 24,
                  height: 24,
                  color: "pink.500",
                  bgcolor: "background.neutral",
                }}
              />
              <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
                {val}
              </Typography>
            </Box>
          );
        },
      },
      {
        id: "created_at",
        accessorKey: "created_at",
        header: "Created at",
        meta: { flex: 1 },
        cell: ({ getValue }) => {
          const val = getValue();
          if (!val) return "";
          const date = new Date(val);
          if (isNaN(date.getTime())) return "";
          return (
            <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
              {format(date, "MM-dd-yyyy")}
            </Typography>
          );
        },
      },
      {
        id: "actions",
        accessorKey: "id",
        header: "Actions",
        size: 120,
        enableSorting: false,
        cell: ({ row }) => (
          <ActionMenu data={row.original} onRefresh={handleRefresh} />
        ),
      },
    ],
    [handleRefresh],
  );

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
        <Typography
          color="text.primary"
          typography="m2"
          fontWeight="fontWeightSemiBold"
        >
          Keys
        </Typography>
        <Typography
          typography="s1"
          color="text.primary"
          fontWeight="fontWeightRegular"
        >
          Your secret API keys are listed below. These keys are encrypted
        </Typography>
      </Box>
      <ApiKeyBar
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        handleCreateClick={() => setOpenCreateApiKey(true)}
      />
      {hasData ? (
        <>
          <DataTable
            columns={columns}
            data={rows}
            isLoading={isLoading}
            rowCount={total}
            sorting={sorting}
            onSortingChange={setSorting}
            getRowId={(row) => row.id}
            rowHeight={40}
            emptyMessage="No keys found"
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
        </>
      ) : (
        <Box
          display="flex"
          flexDirection="column"
          justifyContent="center"
          alignItems="center"
          gap={2}
          height="100%"
        >
          <Image
            src="/assets/icons/blank_table_icon.png"
            sx={{ width: 68, height: 68 }}
          />
          <Box
            display="flex"
            flexDirection="column"
            gap="2px"
            textAlign="center"
          >
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              No keys has been added yet
            </Typography>
            <Typography
              typography="s1"
              fontWeight="fontWeightRegular"
              color="text.primary"
            >
              Click on + Add API Key to create and manage your API key
            </Typography>
          </Box>
        </Box>
      )}
      <CreateApiKey
        open={openCreateApiKey}
        onClose={() => setOpenCreateApiKey(false)}
        refreshGrid={() => handleRefresh()}
      />
    </Box>
  );
}
