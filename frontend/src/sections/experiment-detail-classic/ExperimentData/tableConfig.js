import { parseCellValue } from "src/utils/agUtils";
import { AGGridCellDataType } from "src/utils/constant";
import CustomExperimentColumnHeader from "./CustomExperimentColumnHeader";
import CustomCellRender from "src/sections/common/DevelopCellRenderer/CustomCellRender";
import CustomDevelopGroupCellHeader from "src/sections/common/DevelopCellRenderer/CustomDevelopGroupCellHeader";
import CustomDevelopDetailColumn from "src/sections/common/CustomDevelopDetailColumn";

export const ExperimentDataDefaultColDef = {
  lockVisible: true,
  filter: false,
  resizable: true,
  selectable: true,
  flex: 1,
  cellStyle: {
    padding: 0,
    height: "100%",
    display: "flex",
    flex: 1,
    flexDirection: "column",
  },
};

const DEFAULT_MIN_WIDTH = 300;

export const getEachColumnDef = (
  eachCol,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  removeWaveSurferInstance,
  updateWaveSurferInstance,
  onRerun,
) => {
  const colDataType = eachCol?.data_type ?? eachCol?.dataType;
  const colOriginType = eachCol?.origin_type ?? eachCol?.originType;
  return {
    field: eachCol.id,
    headerName: eachCol.name,
    valueGetter: (v) => {
      const cell = v.data?.[eachCol.id];
      const rawValue = cell?.cell_value ?? cell?.cellValue;
      return parseCellValue(rawValue, AGGridCellDataType[colDataType]);
    },
    cellDataType: AGGridCellDataType[colDataType],
    dataType: colDataType,
    originType: colOriginType || "evaluation",
    col: {
      ...eachCol,
      getWaveSurferInstance: getWaveSurferInstance,
      storeWaveSurferInstance: storeWaveSurferInstance,
      removeWaveSurferInstance: removeWaveSurferInstance,
      updateWaveSurferInstance: updateWaveSurferInstance,
      page: "experiment_data",
    },
    minWidth: 300,
    headerComponent: CustomExperimentColumnHeader,
    cellStyle: {
      padding: 0,
    },
    headerComponentParams: {
      col: eachCol,
      hideMenu: true,
    },
    // mainMenuItems: getMainMenuItems,
    cellRenderer: CustomCellRender,
    resizable: true,
    cellRendererParams: {
      originOfColumn: "experiment", // Pass originOfColumn dynamically to enable rerun at cell level for only experiment originType
      onRerun,
    },
  };
};

export const getIndividualExperimentColumnConfig = ({
  eachCol,
  isHoverButtonVisible = true,
  getMainMenuItems,
  children,
  setFeedBack,
  setImprovement,
  setDatapointDrawerData,
  getWaveSurferInstance,
  storeWaveSurferInstance,
  removeWaveSurferInstance,
  updateWaveSurferInstance,
  setRowNewData,
  editCellRef,
  setEditCell,
  editCell,
  onCellValueChanged,
}) => {
  const colDataType = eachCol?.data_type ?? eachCol?.dataType;
  const colOriginType = eachCol?.origin_type ?? eachCol?.originType;
  const colIsFrozen = eachCol?.is_frozen ?? eachCol?.isFrozen;
  const colIsVisible = eachCol?.is_visible ?? eachCol?.isVisible;
  const isEditable =
    !["run_prompt", "evaluation", "optimization", "annotation_label"].includes(
      colOriginType,
    ) && !["image", "audio"].includes(colDataType);
  const baseConfig = {
    field: eachCol.id,
    headerName: eachCol.name,
    valueGetter: (v) => {
      const cell = v.data?.[eachCol.id];
      const rawValue = cell?.cell_value ?? cell?.cellValue;
      return parseCellValue(rawValue, AGGridCellDataType[colDataType]);
    },
    valueSetter: (params) => {
      const cell = params.data[eachCol.id];
      if (cell && "cell_value" in cell) {
        cell.cell_value = params.newValue;
      } else if (cell) {
        cell.cellValue = params.newValue;
      }
      return true;
    },
    editable: isEditable,
    cellDataType: AGGridCellDataType[colDataType],
    dataType: colDataType,
    pinned: colIsFrozen,
    hide: !colIsVisible,
    minWidth: DEFAULT_MIN_WIDTH,
    // suppressSizeToFit: true,
    originType: colOriginType,
    headerComponent: CustomDevelopDetailColumn,
    headerComponentParams: {
      col: eachCol,
    },
    col: {
      ...eachCol,
      dataType: colDataType,
      originType: colOriginType,
      isFrozen: colIsFrozen,
      isVisible: colIsVisible,
      feedBackClick: setFeedBack,
      improvementClick: setImprovement,
      isHoverButtonVisible: isHoverButtonVisible,
      setDatapointDrawerData: setDatapointDrawerData,
      getWaveSurferInstance: getWaveSurferInstance,
      storeWaveSurferInstance: storeWaveSurferInstance,
      removeWaveSurferInstance: removeWaveSurferInstance,
      updateWaveSurferInstance: updateWaveSurferInstance,
    },
    cellEditor:
      colDataType === "integer"
        ? "agNumberCellEditor"
        : colDataType === "text"
          ? "agLargeTextCellEditor"
          : colDataType === "boolean"
            ? "agRichSelectCellEditor"
            : colDataType === "datetime"
              ? "agDateCellEditor"
              : colDataType === "json"
                ? "JsonCellEditor"
                : "agLargeTextCellEditor",
    cellEditorParams: {
      maxLength: 100000,
      values: colDataType === "boolean" ? ["true", "false"] : undefined,
      onCellValueChanged,
    },
    cellEditorPopup: true,
    mainMenuItems: getMainMenuItems,
    cellRenderer: CustomCellRender,
    cellRendererParams: {
      onEditCell: (params) => {
        editCellRef.current = null;
        setEditCell(params);
      },
      onCellValueChanged,
      editCell,
      editable: true,
    },
    children,
    headerGroupComponent: CustomDevelopGroupCellHeader,
    headerGroupComponentParams: {
      col: eachCol,
    },
    headerClass: "develop-data-group-header",
    setRowNewData: setRowNewData,
  };

  if (colDataType === "datetime") {
    return {
      ...baseConfig,
      valueGetter: (v) => {
        const cell = v.data?.[eachCol.id];
        const rawValue = cell?.cell_value ?? cell?.cellValue;
        return new Date(rawValue);
      },
      valueSetter: (params) => {
        const cell = params.data[eachCol.id];
        const date = new Date(params.newValue);
        if (cell && "cell_value" in cell) {
          cell.cell_value = date;
        } else if (cell) {
          cell.cellValue = date;
        }
        return true;
      },
    };
  }

  return baseConfig;
};
