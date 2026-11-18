/**
 * Evals logs filter <-> shared Filter panel. See datasetColumnAdapter for the
 * value rules; this only says how an Evals grid column describes itself.
 */
import {
  columnsToFields as baseColumnsToFields,
  DATASET_FILTER_CATEGORY,
  fromPanelValue,
  toPanelValue,
} from "src/components/filter/datasetColumnAdapter";

export { DATASET_FILTER_CATEGORY, fromPanelValue, toPanelValue };

export const columnsToFields = (allColumns) =>
  baseColumnsToFields(allColumns, (column) => ({
    id: column?.colId || column?.col?.id,
    label: column?.headerName,
    dataType: column?.data_type,
  }));
