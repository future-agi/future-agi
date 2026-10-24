import { Box, Button, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import { AgGridReact } from "ag-grid-react";
import { format } from "date-fns";
import React, { useMemo, useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { ShowComponent } from "src/components/show";
import { useDebounce } from "src/hooks/use-debounce";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import Image from "src/components/image";
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
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);
  const [searchQuery, setSearchQuery] = useState("");
  const [hasData, setHasData] = useState(true);
  const gridRef = useRef(null);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const [openCreateApiKey, setOpenCreateApiKey] = useState(false);

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

          const rows = data?.status ? data?.result?.table : [];
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
    [debouncedSearchQuery, hasData],
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
        cellRenderer: CellRenderer,
      },
      {
        headerName: "Secret Key",
        field: "secret_key",
        flex: 1,
        cellRenderer: CellRenderer,
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
        valueFormatter: (p) => {
          if (!p.value) return "";
          const date = new Date(p.value);
          return isNaN(date.getTime()) ? "" : format(date, "MM-dd-yyyy");
        },
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
    <Box sx={{ height: "100%" }}>
      <PageHeader
        title="API Keys"
        description="Your secret API keys are listed below. These keys are encrypted"
        sx={{ mb: 0 }}
      />
      <ListToolbar>
        <FormSearchField
          size="small"
          placeholder="Search"
          sx={{ width: 280 }}
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
              width={20}
              icon="solar:add-linear"
              color="background.paper"
            />
          }
        >
          Add API Key
        </Button>
      </ListToolbar>
      <ShowComponent condition={hasData}>
        <Box
          className="ag-theme-quartz"
          style={{ height: "calc(100% - 96px)" }}
        >
          <AgGridReact
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
        <Box
          display="flex"
          flexDirection={"column"}
          justifyContent={"center"}
          alignItems={"center"}
          gap={2}
          height={"100%"}
        >
          <Image
            src="/assets/icons/blank_table_icon.png"
            sx={{ width: 68, height: 68 }}
          />
          <Box
            display="flex"
            flexDirection={"column"}
            gap={"2px"}
            textAlign={"center"}
          >
            <Typography
              typography={"m3"}
              fontWeight={"fontWeightMedium"}
              color="text.primary"
            >
              No keys has been added yet
            </Typography>
            <Typography
              typography={"s1"}
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              Click on + Add API Key to create and manage your API key
            </Typography>
          </Box>
        </Box>
      </ShowComponent>
    </Box>
  );
};

export default ApiKeysLandingPage;
