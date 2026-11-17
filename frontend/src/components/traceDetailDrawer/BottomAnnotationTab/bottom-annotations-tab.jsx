import { Box, Button, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useRef } from "react";
import Iconify from "src/components/iconify";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import AnnotatorsDropdown from "./AnnotatorsDropdown";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ShowComponent } from "src/components/show";
import NoAnnotationLabelsSection from "./NoAnnotationLabelsSection";
import NoAnnotationSection from "./NoAnnotationSection";
import { useTraceDetailContext } from "../TraceDetailContext";
import { useSelectedNode } from "../useSelectedNode";
// import SvgColor from "src/components/svg-color";
import { spanNotesColumnsDefs } from "./helper";
import AnnotationsRowSpanningGrid from "../../Grid/AnnotationsRowSpanningGrid";

const DEFAULT_COL_DEF = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  minWidth: 150,
  suppressMenuHide: true,
  suppressCheckbox: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const BottomAnnotationsTab = ({
  selectedAnnotators,
  setSelectedAnnotators,
  annotatorFilter,
  setAnnotatorFilter,
  spanAnnotations,
  spanNotes,
}) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { projectId, observeId } = useParams();
  const { selectedNode: _selectedNode } = useSelectedNode();
  // const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const projectIdToUse = projectId || observeId;
  const gridRef = useRef(null);
  // const iconStyles = {
  //   width: 16,
  //   height: 16,
  //   color: "text.primary",
  // };

  const { data: labels } = useQuery({
    queryKey: ["project-annotations-labels", projectIdToUse],
    queryFn: () =>
      axios.get(endpoints.project.getAnnotationLabels(), {
        params: { project_id: projectIdToUse },
      }),
    select: (data) => data?.data?.results,
  });

  // useEffect(() => {
  //   if (gridRef.current?.api) {
  //     const visibleColumns = columns.filter(col => col.isVisible);
  //     const columnOrder = visibleColumns.map(col => col.field);
  //     gridRef.current.api.setColumnOrder(columnOrder);
  //   }
  // }, [columns]);

  // const handleOpenColumnConfig = (event) => {
  //   columnConfigureRef.current = event.currentTarget;
  //   setOpenColumnConfigure(true);
  // };

  const { setAddLabelDrawerOpen } = useTraceDetailContext();
  const rows = useMemo(() => {
    return (spanAnnotations || []).map((item) => ({
      id: item.id,
      // Kept camelCase for the AnnotationsRowSpanningGrid sort (uses annotationLabelId)
      annotationLabelId: item.annotationLabelId,
      // snake_case keys below match `field:` in helper.js + AnnotationsRowSpanningGrid
      annotation_name: item.annotationLabelName,
      value: item.annotationValue,
      notes: item.notes || "",
      updated_by: item.annotator,
      updated_at: item?.updatedAt || new Date(),
      type: item.annotationType,
      settings: item?.settings,
    }));
  }, [spanAnnotations]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "13px",
        minHeight: "100%", // Ensure the container takes full height
      }}
    >
      {/* Existing header with dropdown and button */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <AnnotatorsDropdown
          selectedAnnotators={selectedAnnotators}
          setSelectedAnnotators={setSelectedAnnotators}
          annotatorFilter={annotatorFilter}
          setAnnotatorFilter={setAnnotatorFilter}
        />
        <Button
          variant="outlined"
          color="primary"
          size="small"
          startIcon={<Iconify icon="solar:add-linear" width={16} height={16} />}
          onClick={() => setAddLabelDrawerOpen(true)}
        >
          Add Label
        </Button>
      </Box>

      {/* Empty state sections with centered content */}
      <ShowComponent
        condition={labels?.length === 0 && spanAnnotations?.length === 0}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            flexGrow: 1,
            height: "100%",
          }}
        >
          <NoAnnotationLabelsSection
            onCreateLabel={() => setAddLabelDrawerOpen(true)}
          />
        </Box>
      </ShowComponent>

      <ShowComponent
        condition={spanAnnotations?.length === 0 && labels?.length > 0}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            flexGrow: 1,
            height: "100%",
          }}
        >
          <NoAnnotationSection
            onCreateLabel={() => setAddLabelDrawerOpen(true)}
          />
        </Box>
      </ShowComponent>

      {/* Rest of your existing code for tables */}
      <ShowComponent condition={spanAnnotations?.length > 0}>
        {/* <Box
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
            theme={agTheme}
            ref={(params) => {
              gridRef.current = params;
            }}
            domLayout="autoHeight"
            columnDefs={columnDefs}
            defaultColDef={{
              lockVisible: true,
              sortable: false,
              filter: false,
              resizable: true,
              minWidth: 150,
              suppressMenuHide: true,
              suppressCheckbox: true,
              suppressHeaderMenuButton: true,
              suppressHeaderContextMenu: true,
            }}
            paginationPageSizeSelector={false}
            getRowId={(params) => params.data.id}
            suppressCellSelection={true}
            hidePopupMenu={true}
            rowData={rows}
            rowStyle={{ cursor: "pointer" }}
          />
        </Box> */}

        <AnnotationsRowSpanningGrid rowData={rows} gridRef={gridRef} />
      </ShowComponent>
      {spanNotes?.length > 0 && (
        <>
          <Typography variant="s1" fontWeight="fontWeightSemiBold">
            Span Notes
          </Typography>
          <Box
            className="ag-theme-quartz"
            sx={{
              overflowX: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 1,
              "& .ag-center-cols-viewport": {
                minHeight: "unset !important",
              },
            }}
          >
            <AgGridReact
              theme={agTheme}
              domLayout="autoHeight"
              columnDefs={spanNotesColumnsDefs}
              defaultColDef={DEFAULT_COL_DEF}
              paginationPageSizeSelector={false}
              getRowId={(params) => params.data.id}
              suppressCellSelection={true}
              hidePopupMenu={true}
              rowData={spanNotes}
              rowStyle={{ cursor: "pointer" }}
            />
          </Box>
        </>
      )}
      {/* <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef.current}
        columns={annotationColumns}
        setColumns={setAnnotationColumns}
        onColumnVisibilityChange={onColumnVisibilityChange}
        useGrouping={false}
      /> */}
    </Box>
  );
};

BottomAnnotationsTab.propTypes = {
  selectedAnnotators: PropTypes.array,
  setSelectedAnnotators: PropTypes.func,
  annotatorFilter: PropTypes.string,
  setAnnotatorFilter: PropTypes.func,
  spanAnnotations: PropTypes.array,
  spanNotes: PropTypes.array,
};

export default BottomAnnotationsTab;
