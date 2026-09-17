import { useCallback } from "react";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { useRerunDependentColumnsStore } from "../states";
import { findDependentColumns, getColumnDescriptor } from "./dependentColumns";

const useRerunDependentColumns = (dataset) => {
  const allColumns = useDatasetColumnConfig(dataset);
  const setRerunDependentColumns = useRerunDependentColumnsStore(
    (state) => state.setRerunDependentColumns,
  );

  return useCallback(
    (sourceColumnId) => {
      const dependents = findDependentColumns(sourceColumnId, allColumns);
      if (!dependents.length) return;

      const sourceColumn = allColumns.find(
        (column) =>
          String(column?.field ?? column?.id) === String(sourceColumnId),
      );

      setRerunDependentColumns({
        sourceColumn: getColumnDescriptor(sourceColumn, sourceColumnId),
        dependents,
      });
    },
    [allColumns, setRerunDependentColumns],
  );
};

export default useRerunDependentColumns;
