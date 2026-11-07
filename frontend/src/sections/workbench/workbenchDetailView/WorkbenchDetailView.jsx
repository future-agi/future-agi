import SelectionBar from "src/components/selection-bar";
import { Box, Button, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router";
import FormSearchField from "../../../components/FormSearchField/FormSearchField";
import Iconify from "../../../components/iconify";
import { useDebounce } from "../../../hooks/use-debounce";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "../../../utils/axios";

import { CustomCellRender } from "./cell-renderer";
import DeletePrompt from "./DeletePrompt";
import ShowPromptsComponents from "./ShowPromptsComponents";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { APP_CONSTANTS } from "src/utils/constants";

const WorkbenchDetailView = forwardRef(({ setHasData }, ref) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const navigate = useNavigate();
  const [selected, setSelected] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [openDelete, setOpenDelete] = useState(false);
  const [selectedAll, setSelectedAll] = useState(false);
  const [showPromptOptions, setShowPromptOptions] = useState(false);

  const btnRef = useRef(null);
  const { role } = useAuthContext();
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  useEffect(() => {
    if (debouncedSearchQuery !== "") {
      trackEvent(Events.savedPromptSearched, {
        [PropertyName.searchTerm]: debouncedSearchQuery,
      });
    }
  }, [debouncedSearchQuery]);

  const closeModal = () => {
    setOpenDelete(false);
    onSelectionChanged(null);
    ref?.current?.api.deselectAll();
    setSelectedAll(false);
  };

  const refreshGrid = (option = {}) => {
    ref?.current?.api?.refreshServerSide(option);
  };

  const showPromptOption = () => {
    setShowPromptOptions((pre) => !pre);
  };

  const onSelectionChanged = useCallback(
    (event) => {
      if (!event) {
        setTimeout(() => {
          setSelected([]);
        }, 300);
        ref?.current?.api?.deselectAll();
        return;
      }
      const rowId = event?.data?.id;
      trackEvent(Events.selectedSavedPrompt, {
        [PropertyName.id]: rowId,
      });
      setSelected((prevSelectedItems) => {
        const updatedSelectedRowsData = [...prevSelectedItems];

        const rowIndex = updatedSelectedRowsData.findIndex(
          (row) => row?.id === rowId,
        );

        if (rowIndex === -1) {
          updatedSelectedRowsData.push(event.data);
        } else {
          updatedSelectedRowsData.splice(rowIndex, 1);
        }

        return updatedSelectedRowsData;
      });
    },
    [ref],
  );

  const gridOptions = {
    pagination: false,
    rowSelection: { mode: "multiRow" },
    paginationAutoPageSize: false,
  };

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      resizable: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      cellStyle: {
        height: "100%",
        display: "flex",
        flex: 1,
        flexDirection: "column",
      },
    }),
    [],
  );

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Title",
        field: "name",
        flex: 1,
        columnId: "name",
        cellRenderer: CustomCellRender,
      },
      {
        headerName: "Foundation Models",
        field: "model",
        flex: 1,
        columnId: "Models",
        cellRenderer: CustomCellRender,
      },
      {
        headerName: "Last updated time",
        field: "updated_at",
        flex: 1,
        columnId: "date",
        cellRenderer: CustomCellRender,
      },
      {
        headerName: "Collaborators",
        field: "",
        flex: 1,
        columnId: "Collaborators",
        cellRenderer: CustomCellRender,
      },
    ],
    [],
  );

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        onSelectionChanged(null);
        const { request } = params;
        const pageSize = request.endRow - request.startRow;
        const pageNumber = Math.floor(request.startRow / pageSize);

        try {
          const search = debouncedSearchQuery?.length
            ? debouncedSearchQuery
            : "";
          const { data } = await axios.get(
            endpoints.develop.runPrompt.promptExecutions(),
            {
              params: {
                name: search,
                page: pageNumber + 1,
                page_size: pageSize,
              },
            },
          );
          // const data = apiResponse;

          const rows = data?.results ?? [];

          if (!data.count) {
            setHasData(!!debouncedSearchQuery);
          }

          params.success({
            rowData: rows,
            rowCount: data?.count || 0,
          });
        } catch (error) {
          params.fail();
          setHasData(!!debouncedSearchQuery);
        }

        return [];
      },
      getRowId: (data) => data.id,
    }),
    [debouncedSearchQuery, setHasData, onSelectionChanged],
  );

  const id = useMemo(
    () => (showPromptOptions ? `prompt-popper` : undefined),
    [showPromptOptions],
  );

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          sx={{ minWidth: "360px" }}
          searchQuery={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <Box>
          {selected?.length ? (
            <SelectionBar
              count={selected.length}
              onCancel={closeModal}
              actions={[
                {
                  label: "Delete",
                  icon: "solar:trash-bin-trash-linear",
                  disabled: !RolePermission.PROMPTS[PERMISSIONS.DELETE][role],
                  onClick: () => {
                    if (!RolePermission.PROMPTS[PERMISSIONS.DELETE][role])
                      return;
                    setOpenDelete(true);
                  },
                },
              ]}
            />
          ) : (
            <Button
              variant="contained"
              color="primary"
              onClick={showPromptOption}
              endIcon={
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  sx={{ color: "primary.contrastText" }}
                />
              }
              aria-describedby={id}
              ref={btnRef}
            >
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                Create Prompt
              </Typography>
            </Button>
          )}
        </Box>
      </Box>
      <Box
        className="ag-theme-quartz develop-view"
        style={{ height: "100%" }}
        sx={{ "& .ag-root": { position: "sticky" } }}
      >
        <AgGridReact
          ref={ref}
          theme={agTheme}
          defaultColDef={defaultColDef}
          columnDefs={columnDefs}
          pagination={gridOptions.pagination}
          // paginationAutoPageSize={gridOptions.paginationAutoPageSize}
          // paginationPageSizeSelector={false}
          rowSelection={gridOptions.rowSelection}
          isApplyServerSideTransaction={() => true}
          suppressRowClickSelection={true}
          suppressContextMenu={true}
          selectionColumnDef={{ pinned: "left" }}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={10}
          cacheBlockSize={10}
          maxBlocksInCache={10}
          rowModelType="serverSide"
          serverSideStoreType="partial"
          serverSideDatasource={dataSource}
          rowStyle={{ cursor: "pointer" }}
          immutableData={true}
          getRowId={({ data }) => data.id}
          onRowSelected={onSelectionChanged}
          onCellClicked={(event) => {
            if (
              event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              const selected = event.node.isSelected();
              event.node.setSelected(!selected);
            } else {
              const rowId = event?.data?.id;
              if (!rowId) return;
              navigate(`/dashboard/workbench/create/${rowId}`);
            }
          }}
          onColumnHeaderClicked={(event) => {
            if (
              event?.column?.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
            ) {
              if (selectedAll) {
                event.api.deselectAll();
                setSelectedAll(false);
              } else {
                event.api.selectAll();
                setSelectedAll(true);
              }
            }
          }}
        />
      </Box>
      <DeletePrompt
        open={openDelete}
        onClose={closeModal}
        refreshGrid={refreshGrid}
        selected={selected}
      />
      <ShowPromptsComponents
        open={showPromptOptions}
        onClose={() => setShowPromptOptions(false)}
        id={id}
        ref={btnRef}
      />
    </Box>
  );
});

export default WorkbenchDetailView;

WorkbenchDetailView.displayName = "WorkbenchDetailView";

WorkbenchDetailView.propTypes = {
  setHasData: PropTypes.func,
  ref: PropTypes.any,
};
