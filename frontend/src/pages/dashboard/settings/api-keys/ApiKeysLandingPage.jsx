import { Box, Button } from "@mui/material";
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import { AgGridReact } from "ag-grid-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { ShowComponent } from "src/components/show";
import { useDebounce } from "src/hooks/use-debounce";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { fDateLocal } from "src/utils/format-time";
import { LocalDateTime } from "src/components/date-time/LocalDateTime";
import Iconify from "src/components/iconify";
import KeysEmptyState from "src/sections/keys/view/KeysEmptyState";
import { personalKeyRows } from "src/sections/keys/view/personalKeys";
import SecretKeyRenderer from "src/sections/keys/view/SecretKeyRenderer";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import CreateApiKey from "./CreateApiKey";
import CellRenderer from "./CellRenderer";

const defaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const ApiKeysLandingPage = () => {
  const { user } = useSafeAuthContext();
  const { currentOrganizationId } = useOrganization();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);
  const [searchQuery, setSearchQuery] = useState("");
  const [hasData, setHasData] = useState(true);
  const gridRef = useRef(null);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [openCreateApiKey, setOpenCreateApiKey] = useState(false);

  useEffect(() => {
    setHasData(true);
  }, [user?.id, currentOrganizationId]);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        try {
          const apiParams = {
            search: debouncedSearchQuery?.length ? debouncedSearchQuery : null,
            current_page_index: pageNumber,
            page_size: pageSize,
          };

          const { data } = await axios.get(endpoints.keys.getKeys, {
            params: apiParams,
          });

          // Each row keeps the masked hints the list answer sent, so an Owner
          // can tell keys apart.
          const rows = personalKeyRows(
            (data?.status ? data?.result?.table : []) ?? [],
          );
          if (rows.length === 0 && !debouncedSearchQuery) {
            setHasData(false);
          }

          params.success({
            rowData: rows,
          });
        } catch (error) {
          params.fail();
        }
      },
      getRowId: (data) => data.id,
    }),
    [debouncedSearchQuery, user?.id, currentOrganizationId],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Key Name",
        field: "key_name",
        flex: 1,
        cellRenderer: CellRenderer,
      },
      {
        headerName: "API Key",
        field: "api_key",
        flex: 1,
        cellRenderer: (params) => <SecretKeyRenderer value={params.value} />,
      },
      {
        headerName: "Secret Key",
        field: "secret_key",
        flex: 1,
        cellRenderer: (params) => <SecretKeyRenderer value={params.value} />,
      },
      {
        headerName: "Created By",
        field: "created_by",
        flex: 1,
        cellRenderer: CellRenderer,
      },
      {
        headerName: "Created at",
        field: "created_at",
        flex: 1,
        valueFormatter: (params) => fDateLocal(params.value),
        cellRenderer: (params) => (
          <LocalDateTime
            value={params.value}
            emptyText="No creation date recorded"
          />
        ),
      },
      {
        headerName: "Actions",
        field: "id",
        flex: 1,
        cellRenderer: CellRenderer,
      },
    ],
    [],
  );

  const handleCreateClick = () => {
    setOpenCreateApiKey(true);
  };

  const refreshGrid = () => {
    if (gridRef.current) {
      gridRef.current.api.refreshServerSide({ purge: true });
    }
  };

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* On a wide screen this is one row; on a phone the description and the
          controls wrap onto their own rows, so nothing scrolls sideways. */}
      <Box
        data-testid="api-keys-header"
        sx={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <PageHeader
          title="API Keys"
          description="Create and manage API keys to send traces and call Future AGI from your code. API keys are personal. Secrets are shown only when you create a key."
          sx={{ mb: 0, flex: "1 1 260px", minWidth: 0 }}
        />
        <ListToolbar
          data-testid="api-keys-controls"
          sx={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 1.5,
            flex: "1 1 100%",
            minWidth: 0,
          }}
        >
          <FormSearchField
            size="small"
            placeholder="Search"
            sx={{
              flex: "1 1 160px",
              minWidth: 0,
              maxWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
            autoFocus
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <Box sx={{ flex: 1 }} />
          <Button
            onClick={handleCreateClick}
            variant="contained"
            color="primary"
            startIcon={
              <Iconify
                width={16}
                icon="solar:add-linear"
                color="background.paper"
              />
            }
            sx={{
              typography: "s1",
              flexShrink: 0,
              whiteSpace: "nowrap",
            }}
          >
            Add API Key
          </Button>
        </ListToolbar>
      </Box>
      <ShowComponent condition={hasData}>
        {/* Takes the height the header leaves, however many rows it wraps to. */}
        <Box
          className="ag-theme-quartz"
          style={{ flex: 1, minHeight: 0, paddingTop: "12px" }}
        >
          <AgGridReact
            key={`${user?.id}:${currentOrganizationId}`}
            ref={gridRef}
            getRowHeight={40}
            rowSelection="none"
            theme={agTheme}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pagination={false}
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={5}
            cacheBlockSize={10}
            maxBlocksInCache={10}
            suppressRowClickSelection={true}
            rowModelType="serverSide"
            serverSideDatasource={dataSource}
            isApplyServerSideTransaction={() => true}
            getRowId={({ data }) => data.id}
          />
        </Box>
      </ShowComponent>
      <CreateApiKey
        open={openCreateApiKey}
        onClose={() => setOpenCreateApiKey(false)}
        refreshGrid={() => {
          refreshGrid();
          setHasData(true);
        }}
      />
      <ShowComponent condition={!hasData}>
        <KeysEmptyState onCreate={handleCreateClick} />
      </ShowComponent>
    </Box>
  );
};

export default function ScopedApiKeysPage() {
  const { user } = useSafeAuthContext();
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  return (
    <ApiKeysLandingPage
      key={`${user?.id}:${currentOrganizationId}:${currentWorkspaceId}`}
    />
  );
}
