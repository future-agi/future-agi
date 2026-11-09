// Group by on the dataset list as DataTable groups (W9-SCOPE §5 A3): the
// query's groups become DataTable's `groups`, and `Show all 37` on a group
// larger than the page adds that group's value as a filter chip (one
// mechanism: the filter). Pure.
import { CONTRACT_TYPE } from "src/sections/common/ListFilter/listFilterModel";
import { folderCondition } from "./folderRailModel";

/** A group key back as the field's value (metadata numbers / booleans). */
function valueOf(field, key) {
  if (field?.type === "number") return Number(key);
  if (field?.type === "boolean") return key === "true";
  return key;
}

/**
 * The filter condition that keeps one group's datasets: `Owner is Asha
 * Rao`, `Tag is golden`, `Folder is support`, `Tag is empty` for the
 * `No tag` group.
 * @param {{id: string, type: string}} field  the Group by field
 * @param {{key: string}} group
 */
export function groupCondition(field, group) {
  const key = String(group?.key ?? "");
  // A folder group holds that folder's own datasets: `Folder is support`
  // (the rail's "is under" would add its subfolders).
  if (field?.type === "folder")
    return key
      ? {
          column_id: field.id,
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: key,
          },
        }
      : folderCondition("");
  const filterType = CONTRACT_TYPE[field?.type] ?? "categorical";
  if (key === "")
    return {
      column_id: field.id,
      filter_config: {
        filter_type: filterType,
        filter_op: "is_null",
        filter_value: null,
      },
    };
  if (filterType === "categorical")
    return {
      column_id: field.id,
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: [key],
      },
    };
  return {
    column_id: field.id,
    filter_config: {
      filter_type: filterType,
      filter_op: "equals",
      filter_value: valueOf(field, key),
    },
  };
}

/**
 * The query's groups as DataTable groups: rows, and `onShowAll` on a group
 * that holds more datasets than it shows.
 * @param {{key, label, count, below_baseline, datasets}[]} groups
 * @param {{field: object, filters: object[], onFilters: (next) => void}} opts
 */
export function tableGroups(groups, { field, filters, onFilters }) {
  if (!Array.isArray(groups)) return undefined;
  return groups.map((g) => {
    const rows = g.datasets ?? [];
    return {
      key: String(g.key),
      label: g.label,
      count: g.count,
      below_baseline: g.below_baseline ?? 0,
      rows,
      ...(field && onFilters && g.count > rows.length
        ? {
            onShowAll: () =>
              onFilters([...(filters ?? []), groupCondition(field, g)]),
          }
        : {}),
    };
  });
}
