import { Box, Button, Divider, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { format } from "date-fns";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
  const [searchParams] = useSearchParams();
  const shouldAutoOpenCreateApiKey =
    searchParams.get("source") === "onboarding" &&
    searchParams.get("target") === "observe_first_trace" &&
    searchParams.get("action") === "create";
  const onboardingKeyName = shouldAutoOpenCreateApiKey
    ? searchParams.get("key_name")?.trim()
    : "";
  const rawOnboardingReturnHref = shouldAutoOpenCreateApiKey
    ? searchParams.get("return_to")?.trim()
    : "";
  const onboardingCompletionHref =
    rawOnboardingReturnHref?.startsWith("/dashboard/observe") &&
    !rawOnboardingReturnHref.startsWith("//")
      ? rawOnboardingReturnHref
      : "";

  useEffect(() => {
    if (shouldAutoOpenCreateApiKey) {
      setOpenCreateApiKey(true);
    }
  }, [shouldAutoOpenCreateApiKey]);

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
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography
          typography={"s3"}
          fontWeight={"fontWeightRegular"}
          color="text.primary"
        >
          Your secret API keys are listed below. These keys are encrypted
        </Typography>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 1.5,
          }}
        >
          <FormSearchField
            size="small"
            placeholder="Search"
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
            autoFocus
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <Divider orientation="vertical" variant="middle" flexItem />
          <Box>
            <Button
              onClick={handleCreateClick}
              variant="contained"
              color="primary"
              startIcon={
                <Iconify
                  icon="octicon:plus-24"
                  color="background.paper"
                  sx={{
                    width: "20px",
                    height: "20px",
                  }}
                />
              }
              sx={{ px: "24px", typography: "s1" }}
            >
              Add API Key
            </Button>
          </Box>
        </Box>
      </Box>
      <ShowComponent condition={hasData}>
        <Box
          className="ag-theme-quartz"
          style={{ height: "calc(100% - 40px)", paddingTop: "12px" }}
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
        completionHref={onboardingCompletionHref}
        initialKeyName={onboardingKeyName}
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
