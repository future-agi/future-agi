import React, { useMemo } from "react";
import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/utils/ag-grid-setup";
import { computeSpanInfo } from "./common";
import AnnotationValueCellRenderer from "../traceDetailDrawer/CustomRenderer/AnnotationValueCellRenderer";
import AnnotatorCellRenderer from "../traceDetailDrawer/CustomRenderer/AnnotatorCellRenderer";
import { NotesCellRenderer } from "../traceDetailDrawer/CustomRenderer/NotesCellRenderer";
import PropTypes from "prop-types";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

import CustomHeaderComponent from "./CustomHeaderComponent";
import { formatStartTimeByRequiredFormat } from "src/utils/utils";

const DEFAULT_COL_DEF = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  minWidth: 150,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  headerComponent: CustomHeaderComponent,
};

const AnnotationsRowSpanningGrid = ({ rowData, gridRef }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const sortedRowData = useMemo(
    () =>
      [...rowData].sort((a, b) =>
        a?.annotationLabelId.localeCompare(b?.annotationLabelId),
      ),
    [rowData],
  );

  const spanMap = useMemo(
    () => computeSpanInfo(sortedRowData),
    [sortedRowData],
  );
  const columnDefs = useMemo(
    () => [
      {
        id: "annotation_name",
        name: "Annotation Name",
        field: "annotation_name",
        isVisible: true,
        flex: 1,
        headerName: "Annotation Name",
        headerComponent: CustomHeaderComponent,
        rowSpan: (params) => {
          const info = spanMap.get(params.node.rowIndex);
          return info?.span ?? 1;
        },
        valueGetter: (params) => {
          const info = spanMap.get(params.node.rowIndex);
          return info?.isFirst ? params?.data?.annotation_name : "";
        },
        cellStyle: (params) => {
          const info = spanMap.get(params.node.rowIndex);
          if (info?.span > 1) {
            return {
              backgroundColor: "var(--bg-paper)",
              display: "flex",
              alignItems: "center",
              zIndex: 1,
              borderBottom: "1px solid var(--border-default)",
              borderRight: "1px solid var(--border-default)",
            };
          }
          return null;
        },
      },
      {
        id: "value",
        name: "Value",
        field: "value",
        isVisible: true,
        flex: 1,
        headerName: "Value",
        cellStyle: {
          whiteSpace: "normal",
        },
        headerComponent: CustomHeaderComponent,
        cellRenderer: AnnotationValueCellRenderer,
      },
      {
        id: "notes",
        name: "Notes",
        field: "notes",
        isVisible: true,
        flex: 1,
        headerName: "Notes",
        headerComponent: CustomHeaderComponent,
        cellRenderer: NotesCellRenderer,
        valueFormatter: (params) => params.value || "",
      },
      {
        id: "updatedBy",
        name: "Updated By",
        field: "updated_by",
        isVisible: true,
        flex: 1,
        headerName: "Updated By",
        headerComponent: CustomHeaderComponent,
        cellRenderer: AnnotatorCellRenderer,
      },
      {
        id: "updated_at",
        name: "Updated At",
        field: "updated_at",
        isVisible: true,
        flex: 1,
        headerName: "Updated At",
        headerComponent: CustomHeaderComponent,
        cellStyle: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        },
        valueFormatter: (params) => {
          return (
            formatStartTimeByRequiredFormat(
              new Date(params.value),
              "dd-MM-yy hh:ss",
            ) ?? ""
          );
        },
      },
    ],
    [spanMap],
  );
  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        overflowX: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        "& .ag-root-wrapper": {
          minHeight: "0px !important",
        },
        "& .ag-center-cols-viewport": {
          minHeight: "unset !important",
        },
      }}
    >
      <AgGridReact
        onRowDataUpdated={(params) => params.api.redrawRows()}
        theme={agTheme}
        rowHeight={70}
        ref={(params) => {
          gridRef.current = params;
        }}
        domLayout="autoHeight"
        columnDefs={columnDefs}
        defaultColDef={DEFAULT_COL_DEF}
        paginationPageSizeSelector={false}
        getRowId={(params) => params.data.id}
        rowData={sortedRowData}
        suppressRowTransform={true}
        rowStyle={{ cursor: "pointer" }}
      />
    </Box>
  );
};

export default AnnotationsRowSpanningGrid;

AnnotationsRowSpanningGrid.propTypes = {
  rowData: PropTypes.array,
  gridRef: PropTypes.any,
};
