import React, { useMemo, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Box, Button, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import AddCustomModal from "./AddCustomModal";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { preventHeaderSelection } from "src/utils/utils";
import { useDebounce } from "src/hooks/use-debounce";
import { AgGridReact } from "ag-grid-react";
import { fDateLocal } from "src/utils/format-time";
import { LocalDateTime } from "src/components/date-time/LocalDateTime";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events } from "src/utils/Mixpanel";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { APP_CONSTANTS } from "src/utils/constants";

const defaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const gridOptions = {
  pagination: true,
  rowSelection: { mode: "multiRow" },
  paginationAutoPageSize: true,
};

const UsersCustomModel = () => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const gridRef = useRef();
  const [searchQuery, setSearchQuery] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedAll, setSelectedAll] = useState(false);
  const [selected, setSelected] = useState([]);
  const [openDelete, setOpenDelete] = useState(false);
  const performedClicks = useRef(0);
  const clickTimeout = useRef(null);
  const [modelDrawerData, setModelDrawerData] = useState(null);

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  preventHeaderSelection();

  const handleCreate = () => {
    trackEvent(Events.addCustomModelClicked);
    setShowCreateModal(true);
  };

  const refreshGrid = () => {
    gridRef?.current?.api?.refreshServerSide({});
  };

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;
        onSelectionChanged(null);
        setSelectedAll(false);
        // request has startRow and endRow get next page number and each page has 10 rows
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        try {
          const { data } = await axios.get(
            endpoints.settings.customModal.getCustomModal,
            {
              params: {
                search_query: debouncedSearchQuery?.length
                  ? debouncedSearchQuery
                  : null,
                page: pageNumber + 1,
                page_size: pageSize,
              },
            },
          );

          const rows = data?.results;

          params.success({
            rowData: rows,
          });
        } catch (error) {
          params.fail();
        }
      },
      getRowId: (data) => data.id,
    }),
    [debouncedSearchQuery],
  );

  const { mutate: deleteModels, isPending: deleteLoading } = useMutation({
    mutationFn: async () => {
      const ids = selected.map((item) => item.id);
      return axios.delete(endpoints.settings.customModal.deleteModel, {
        data: { ids: ids },
      });
    },
    onSuccess: () => {
      enqueueSnackbar("Model has been deleted", { variant: "success" });
      setOpenDelete(false);
      onSelectionChanged(null);
      refreshGrid();
    },
  });

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Model Name",
        field: "user_model_id",
        flex: 1,
      },
      {
        headerName: "Provider",
        field: "provider",
        flex: 1,
      },
      // {
      //   headerName: "Input Token Cost",
      //   field: "inputTokenCost",
      //   flex: 1,
      // },
      // {
      //   headerName: "Output Token Cost",
      //   field: "outputTokenCost",
      //   flex: 1,
      // },
      {
        headerName: "Date Added",
        field: "created_at",
        flex: 1,
        valueFormatter: (params) => fDateLocal(params.value),
        cellRenderer: (params) => (
          <LocalDateTime
            value={params.value}
            emptyText="No date added recorded"
          />
        ),
      },
    ],
    [],
  );

  const onSelectionChanged = (event) => {
    if (!event) {
      setTimeout(() => {
        setSelected([]);
      }, 300);
      gridRef?.current?.api?.deselectAll();
      return;
    }
    const rowId = event.data.id;

    setSelected((prevSelectedItems) => {
      const updatedSelectedRowsData = [...prevSelectedItems];

      const rowIndex = updatedSelectedRowsData.findIndex(
        (row) => row.id === rowId,
      );

      if (rowIndex === -1) {
        updatedSelectedRowsData.push(event.data);
      } else {
        updatedSelectedRowsData.splice(rowIndex, 1);
      }

      return updatedSelectedRowsData;
    });
  };

  const debounceCellClick = (handler, event, delay = 250) => {
    performedClicks.current++;
    clickTimeout.current = setTimeout(() => {
      if (performedClicks.current === 1) {
        performedClicks.current = 0;
        handler(event);
      } else {
        performedClicks.current = 0;
      }
    }, delay);
    if (performedClicks.current > 1 && clickTimeout.current) {
      clearTimeout(clickTimeout.current);
    }
  };

  const handleEdit = (event) => {
    const eventData = event.data;
    const data = {
      id: eventData.id,
    };
    setModelDrawerData(data);
  };

  return (
    <>
      <Helmet>
        <title>Custom Models</title>
      </Helmet>
      <Typography
        variant="subtitle1"
        sx={{
          fontWeight: "600",
          fontSize: "14px",
          textAlign: "left",
          marginBottom: "15px",
          color: "text.primary",
        }}
      >
        Custom Models
      </Typography>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          height: "100%",
        }}
      >
        <HelperText text="Models configured here will be available to use throughout the application" />
        <Box
          sx={{
            paddingX: 0,
            paddingY: 2,
            display: "flex",
            gap: 2,
            width: "100%",
            justifyContent: "space-between",
          }}
        >
          <FormSearchField
            autoFocus
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            size="small"
            sx={{ width: 400 }}
            placeholder="Search"
          />
          {selected?.length > 0 ? (
            <Box
              sx={{
                padding: "6px 16px",
                gap: "16px",
                borderRadius: "8px",
                border: "1px solid rgba(225, 223, 236, 1)",
                display: "flex",
              }}
            >
              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: 500,
                  lineHeight: "22px",
                  color: "rgba(120, 87, 252, 1)",
                  paddingRight: "16px",
                  borderRight: "2px solid rgba(225, 223, 236, 1)",
                }}
              >
                {selected?.length || 0} Selected
              </Typography>
              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "text.secondary",
                  display: "flex",
                  alignItems: "center",
                  gap: "5px",
                  cursor: "pointer",
                }}
                onClick={() => setOpenDelete(true)}
              >
                <Iconify icon="solar:trash-bin-trash-bold" />
                Delete
              </Typography>

              <Typography
                sx={{
                  fontSize: "14px",
                  fontWeight: 600,
                  color: "text.secondary",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                  cursor: "pointer",
                }}
                onClick={() => onSelectionChanged(null)}
              >
                Cancel
              </Typography>
            </Box>
          ) : (
            <Button
              id="add-Custom-Model"
              variant="contained"
              color="primary"
              sx={{ marginLeft: 2, padding: "0 20px" }} // Add margin to the left of the button
              onClick={handleCreate}
            >
              Add Custom Model
            </Button>
          )}
        </Box>

        <Box className="ag-theme-quartz" style={{ height: "100%" }}>
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            onColumnHeaderClicked={(event) => {
              if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
                return;

              if (selectedAll) {
                event.api.deselectAll();
                setSelectedAll(false);
              } else {
                event.api.selectAll();
                setSelectedAll(true);
              }
            }}
            // onGridReady={addListener}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pagination={gridOptions.pagination}
            paginationAutoPageSize={gridOptions.paginationAutoPageSize}
            rowSelection={gridOptions.rowSelection}
            suppressRowClickSelection={true}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            serverSideDatasource={dataSource}
            maxBlocksInCache={1}
            onRowSelected={onSelectionChanged}
            getRowId={({ data }) => data.id}
            onCellClicked={(event) => {
              if (
                event.column.getColId() ===
                APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
              ) {
                const selected = event.node.isSelected();
                event.node.setSelected(!selected);
              } else {
                debounceCellClick(() => {
                  handleEdit(event);
                }, event);
              }
            }}
            rowStyle={{ cursor: "pointer" }}
          />
        </Box>

        <AddCustomModal
          open={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onRefresh={refreshGrid}
          data={null}
          edit={false}
        />
        <AddCustomModal
          open={Boolean(modelDrawerData)}
          onClose={() => setModelDrawerData(null)}
          onRefresh={refreshGrid}
          data={modelDrawerData}
          edit={true}
        />
        <ConfirmDialog
          open={openDelete}
          onClose={() => setOpenDelete(false)}
          title="Delete"
          content="Are you sure want to delete?"
          action={
            <LoadingButton
              variant="contained"
              color="error"
              onClick={deleteModels}
              loading={deleteLoading}
            >
              Delete
            </LoadingButton>
          }
        />
      </Box>
    </>
  );
};

export default UsersCustomModel;
