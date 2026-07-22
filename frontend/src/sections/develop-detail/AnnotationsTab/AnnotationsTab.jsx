// @ts-nocheck
import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useMemo,
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import {
  annotationsDefaultColDef,
  annotationsTabColumnDefs,
} from "./tableConfig.js";
import PropTypes from "prop-types";
import AnnotationTabSkeleton from "../Common/Skeletons/AnnotationTabSkeleton.jsx";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { preventHeaderSelection } from "src/utils/utils.js";
import { trackEvent, Events } from "src/utils/Mixpanel";
import RunAnnotations from "../Annotations/RunAnnotations";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { APP_CONSTANTS } from "src/utils/constants";
import { normalizeAnnotationListRow } from "./annotationPreviewShape";

const AnnotationsTab = ({ setSelectedRowsCount, setSelectedRowIds }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const navigate = useNavigate();
  const [, setAnchorEl] = useState(null);
  const [, setCurrentData] = useState([]);
  const { dataset } = useParams();
  const [renderAnnotationTable, setRenderAnnotationTable] = useState(false);
  const gridApiRef = useRef(null);
  const queryClient = useQueryClient();
  preventHeaderSelection();
  const { role } = useAuthContext();

  useEffect(() => {
    // Function to refresh the grid
    const refreshGrid = () => {
      queryClient.invalidateQueries(["annotationList"]);
    };

    // Trigger refreshGrid whenever renderAnnotationTable changes
    refreshGrid();
  }, [renderAnnotationTable, queryClient]);

  // Fetch annotations
  const { isLoading, data: apiData } = useQuery({
    queryKey: [`annotationList-${dataset}`],
    queryFn: () =>
      axios.get(endpoints.annotation.annotationsListByDataSetId(dataset)),
    select: (d) => d.data?.results,
  });

  const handleSetRenderAnnotationTable = useCallback(() => {
    setRenderAnnotationTable((prev) => !prev);
  }, [setRenderAnnotationTable]);

  // Map API data to grid rows
  const dataList = useMemo(() => {
    const data = apiData;
    if (!data) return [];
    return data.map((row) => {
      const i = normalizeAnnotationListRow(row);
      const completed = i.summary?.completed ?? 0;
      const total = i.summary?.total ?? 0;

      return {
        id: i.id,
        name: i.name,
        noOfAnnotations: i.labels?.length,
        status: {
          progress: total ? (completed / total) * 100 : 0,
          text: `${completed}/${total} Completed`,
        },
        peopleAssigned: {
          name: i.assignedUsers[0]?.name,
          otherCount: Math.max(i.assignedUsers?.length - 1, 0),
        },
        assignedUsers: i.assignedUsers,
        created_at: i.created_at,
        actions: { annotationId: i.id, handleSetRenderAnnotationTable },
        lowestUnfinishedRow: i.lowestUnfinishedRow,
      };
    });
  }, [apiData, handleSetRenderAnnotationTable]);

  const handleOpenModal = (el, data) => {
    setAnchorEl(el);
    setCurrentData(data);
  };

  const handleSelectionChanged = () => {
    trackEvent(Events.annSelected);
    const selectedRows = gridApiRef.current.api.getSelectedRows();
    const selectedIds = selectedRows.map((row) => row.id);

    // Update the parent state with the count and selected IDs
    setSelectedRowsCount(selectedRows.length);
    setSelectedRowIds(selectedIds);
  };

  if (isLoading) {
    return <AnnotationTabSkeleton />;
  }

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
        height: "400px",
        overflowY: "auto",
      }}
    >
      {/* AgGridReact */}
      <AgGridReact
        ref={gridApiRef}
        // rowSelection="multiple" // Enable multiple row selection
        theme={agTheme}
        onColumnHeaderClicked={(event) => {
          if (event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
            // Get all displayed rows
            const displayedNodes = [];
            event.api.forEachNode((node) => {
              if (node.displayed) {
                displayedNodes.push(node);
              }
            });

            // Check if all displayed rows are selected
            const allSelected = displayedNodes.every((node) =>
              node.isSelected(),
            );

            // Toggle selection based on current state
            if (allSelected) {
              event.api.deselectAll();
            } else {
              event.api.selectAll();
            }
          }
        }}
        columnDefs={annotationsTabColumnDefs}
        defaultColDef={annotationsDefaultColDef}
        context={{ handleOpenModal }}
        onCellClicked={(event) => {
          if (
            event?.colDef?.field === "name" ||
            event?.colDef?.field === "noOfAnnotations" ||
            event?.colDef?.field === "status" ||
            event?.colDef?.field === "created_at"
          ) {
            if (!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]) {
              return;
            }
            const id = event?.data?.id;
            const lowestUnfinishedRow = event?.data?.lowestUnfinishedRow;
            navigate(
              `/dashboard/develop/${dataset}/preview/${id}?annotationIndex=${lowestUnfinishedRow}`,
              { state: { from: location.pathname } },
            );
          } else if (
            event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
          ) {
            const selected = event.node.isSelected();
            event.node.setSelected(!selected);
          }
        }}
        pagination={true}
        rowData={Array.isArray(dataList) ? dataList : []}
        onSelectionChanged={handleSelectionChanged}
        paginationAutoPageSize={true}
        suppressRowClickSelection={true}
        paginationPageSizeSelector={false}
        suppressNoRowsOverla={true}
        //   rowModelType="serverSide"
        //   serverSideDatasource={dataSource}
        // maxBlocksInCache={1}
        rowSelection={{ mode: "multiRow" }}
        onRowClicked={() => {
          // const { data } = event;
        }}
        rowStyle={{ cursor: "pointer" }}
        domLayout="normal"
      />

      {/* Popover for People Assigned */}
      {/* <Popover
        anchorEl={anchorEl}
        anchorOrigin={{ horizontal: "left", vertical: "bottom" }}
        open={openModal}
        onClose={handleCloseModal}
        MenuListProps={{
          "aria-labelledby": "basic-button",
        }}
        sx={{
          "& .MuiPopover-paper": {
            padding: 0,
          },
        }}
      >
        <Box sx={{ minHeight: 285, minWidth: 200 }}>
          <SearchModal peopleAssigned={dataList.map((i) => i.peopleAssigned)} />
        </Box>
      </Popover> */}

      <RunAnnotations setRenderAnnotationTable={setRenderAnnotationTable} />
    </Box>
  );
};

AnnotationsTab.propTypes = {
  setSelectedRowsCount: PropTypes.func.isRequired,
  setSelectedRowIds: PropTypes.func.isRequired,
};

export default AnnotationsTab;
