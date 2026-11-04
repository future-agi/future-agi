import { OutputTypes } from "../../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

export const getSuffixForCharts = (header, considerIndex = null) => {
  //this is a temporary implementation
  let newHeader = header;
  if (considerIndex || Array.isArray(header)) {
    const target = header?.[considerIndex || 0];
    newHeader = {
      ...target,
      isNumericEval:
        (target?.outputType ?? target?.output_type) === OutputTypes.NUMERIC,
      isNumericEvalPercentage:
        target?.isNumericEvalPercentage ?? target?.is_numeric_eval_percentage,
    };
  }

  if (newHeader?.isNumericEvalPercentage) {
    return "%";
  } else if (newHeader?.isNumericEval) {
    return "";
  } else {
    return "%";
  }
};
