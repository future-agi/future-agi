import { OutputTypes } from "../../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

export const chartColors = [
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#FF0000",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#00FF00",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#0000FF",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#FFFF00",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#00FFFF",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#FF00FF",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#C0C0C0",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#800000",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#808000",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#008000",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#800080",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#008080",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#000080",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#CD5C5C",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#C71585",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#4B0082",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#00FA9A",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#FF6347",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#008080",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#FFA500",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#AA82EE",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#7CFC00",
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  "#20B2AA",
];
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
