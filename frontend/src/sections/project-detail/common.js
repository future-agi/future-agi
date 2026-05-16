import { getRandomId } from "src/utils/utils";
import { z } from "zod";
import { PrototypeObserveColType } from "src/utils/constants";

import { avoidDuplicateFilterSet } from "../../components/ComplexFilter/common";

import ProjectCustomCellRenderer from "./TableCustomComponent/ProjectCustomCellRenderer";

export const getRunListColumnDefs = (col) => {
  return {
    headerName: col.name,
    field: col.id,
    hide: !col?.isVisible,
    cellRenderer: ProjectCustomCellRenderer,
    col,
    valueFormatter: (params) => {
      // if (params?.colDef?.headerName === "Avg. Latency")
      //   return params.value ? `${params.value}s` : "";

      // if (params?.colDef?.col?.groupBy === "Evaluation Metrics")
      //   return params.value !== null ? `${params.value}%` : "";

      return params.value;
    },
    colSpan: (params) => {
      if (
        params?.node?.rowPinned === "bottom" &&
        params?.colDef?.col?.name === "Score"
      ) {
        return 4;
      }
      return 1;
    },
  };
};

// [
//   {
//     "propertyName": "Node Type",
//     "propertyId" : "" // parent property ID for which the filter value in eventually set
//     "filterType" : "filterType" // filter type (options, number)
//     "dependents" : [
//       {
//         "stringConnector" : "is",
//         "propertyName": "Dependent property name 1",
//         "propertyId" : "" // dependent property ID (on priority) for which the filter value in eventually set
//       },
//       {
//         "stringConnector" : "is",
//         "propertyName": "Dependent property name 2",
//         "propertyId" : "" // dependent property ID (on priority) for which the filter value in eventually set
//       }
//     ]
//   },
//   {
//     "propertyName": "Name of property 2",
//   },
// ];

export const AllowedGroups = [
  "System Metrics",
  "Evaluation Metrics",
  "Annotation Metrics",
];
const allowedColumn = ["rank"];

export const generateFilterDefinition = (columns) => {
  const finalDefinition = [];
  const filteredColumns = columns.filter(
    (col) =>
      AllowedGroups.includes(col.groupBy) || allowedColumn.includes(col.id),
  );
  const grouped = {};
  filteredColumns.forEach((col) => {
    if (grouped[col.groupBy]) {
      grouped[col.groupBy].push(col);
    } else {
      grouped[col.groupBy] = [col];
    }
  });
  Object.entries(grouped).forEach(([group, columns]) => {
    if (!AllowedGroups.includes(group) && columns.length === 1) {
      // individual Column
      const col = columns[0];
      finalDefinition.push({
        propertyName: col.name,
        propertyId: col.id,
        maxUsage: 1,
        filterType: {
          type: "number",
        },
      });
    } else {
      // dependent Column
      const obj = {
        propertyName: group,
        stringConnector: "is",
        dependents: columns.map((col) => ({
          propertyName: col.name,
          propertyId: col.id,
          maxUsage: 1,
          filterType: {
            type: "number",
          },
        })),
      };
      finalDefinition.push(obj);
    }
  });

  return finalDefinition;
};

export const applyQuickFilters =
  (setFilters, openQuickFilter, setFilterOpen) =>
  ({ col, value, filterAnchor }) => {
    let filter = null;
    if (!col.groupBy) {
      filter = {
        column_id: col.id,
        filter_config: {
          filter_type: "number",
          filter_op: "equals",
          filter_value: [value, ""],
        },
        _meta: {
          parentProperty: col.id,
        },
        id: getRandomId(),
      };
    } else if (col?.groupBy === "Evaluation Metrics") {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          column_id: col.id,
          filter_config: {
            filter_type: "number",
            filter_op: "equals",
            filter_value: [value, ""],
          },
          _meta: {
            parentProperty: "Evaluation Metrics",
            "Evaluation Metrics": col.id,
          },
          id: getRandomId(),
        },
      });
    } else if (col?.groupBy === "System Metrics") {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          column_id: col.id,
          filter_config: {
            filter_type: "number",
            filter_op: "equals",
            filter_value: [value, ""],
          },
          _meta: {
            parentProperty: "System Metrics",
            "System Metrics": col.id,
          },
          id: getRandomId(),
        },
      });
    } else if (col?.groupBy === "Annotation Metrics") {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          column_id: col.id,
          filter_config: {
            filter_type: "number",
            filter_op: "equals",
            filter_value: [value, ""],
          },
          _meta: {
            parentProperty: "Annotation Metrics",
            "Annotation Metrics": col.id,
          },
          id: getRandomId(),
        },
      });
    }

    if (filter) {
      setFilterOpen(true);
      setFilters((prev) => avoidDuplicateFilterSet(prev, filter));
    }
  };

export const projectSchema = z.object({
  projectName: z
    .string()
    .min(1, { message: "Project Name cannot be empty" })
    .trim(),
  samplingRate: z.number().min(0).max(100).optional(),
});

export const getFilterExtraProperties = (val) => {
  const colType = PrototypeObserveColType?.[val._meta.parentProperty];
  if (!colType) {
    return {};
  }
  return {
    col_type: colType,
  };
};
