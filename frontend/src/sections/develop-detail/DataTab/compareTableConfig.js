import { parseCellValue } from "src/utils/agUtils";
import { AGGridCellDataType } from "src/utils/constant";
import CustomCompareColumnHeader from "./CustomCompareColumnHeader";
import CustomCellRender from "src/sections/common/DevelopCellRenderer/CustomCellRender";

export const ExperimentDataDefaultColDef = {
  filter: false,
  resizable: true,
  flex: 1,
  cellStyle: {
    padding: 0,
    height: "100%",
    display: "flex",
    flex: 1,
    flexDirection: "column",
  },
};

export const getEachCompareColumnDef = (eachCol, diffModeRef) => {
  const colDataType = eachCol?.data_type;
  const colOriginType = eachCol?.origin_type;
  return {
    field: eachCol.id,
    headerName: eachCol.name,
    valueGetter: (v) => {
      const isDiffMode = diffModeRef?.current;
      const cell = v.data?.[eachCol.id];
      let valueToParse;
      let dataTypeToUse;

      const diffValue = cell?.cell_diff_value;
      if (isDiffMode && diffValue) {
        valueToParse = diffValue;
        dataTypeToUse = "";
      } else {
        valueToParse = cell?.cell_value;
        dataTypeToUse = AGGridCellDataType[colDataType];
      }

      return parseCellValue(valueToParse, dataTypeToUse);
    },
    cellDataType: AGGridCellDataType[colDataType],
    dataType: colDataType,
    originType: colOriginType || "evaluation",
    col: eachCol,
    minWidth: 300,
    headerComponent: CustomCompareColumnHeader,
    cellStyle: {
      padding: 0,
    },
    headerComponentParams: {
      col: eachCol,
      hideMenu: true,
    },
    // mainMenuItems: getMainMenuItems,
    cellRenderer: CustomCellRender,
  };
};
