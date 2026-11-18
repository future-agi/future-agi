import { endOfToday, sub } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { getRandomId } from "src/utils/utils";

const COL_TYPE_TO_CATEGORY = {
  SPAN_ATTRIBUTE: "attribute",
  SYSTEM_METRIC: "system",
  EVAL_METRIC: "eval",
  ANNOTATION: "annotation",
};

export const toAddEvalsFormRows = (sourceFilters = []) => {
  const out = [];
  (sourceFilters || []).forEach((f) => {
    const field = f?.column_id;
    if (!field || field === "created_at") return;
    const cfg = f?.filter_config || {};
    const category = COL_TYPE_TO_CATEGORY[cfg.col_type] ?? "system";
    const isAttribute = category === "attribute";
    const raw = cfg.filter_value;
    const values = Array.isArray(raw)
      ? raw
      : typeof raw === "string"
        ? raw
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean)
        : raw != null
          ? [raw]
          : [];
    values.forEach((v) => {
      if (v === "" || v == null) return;
      out.push({
        id: getRandomId(),
        property: isAttribute ? "attributes" : field,
        propertyId: field,
        ...(f?.property_id || f?.registryId
          ? { registryId: f.property_id || f.registryId }
          : {}),
        fieldCategory: category,
        fieldLabel: field,
        ...(cfg.col_type ? { apiColType: cfg.col_type } : {}),
        filterConfig: {
          filterType: cfg.filter_type === "number" ? "number" : "text",
          filterOp: cfg.filter_op || "equals",
          filterValue: v,
          ...(cfg.col_type ? { colType: cfg.col_type } : {}),
        },
      });
    });
  });
  return out;
};

export function buildAddEvalsDraft({
  observeId,
  rowType,
  mainFilters = [],
  extraFilters = [],
  dateFilter,
  returnTo,
}) {
  const filters = [
    ...toAddEvalsFormRows(mainFilters),
    ...toAddEvalsFormRows(extraFilters),
  ];
  const startDate =
    dateFilter?.dateFilter?.[0] ?? formatDate(sub(new Date(), { months: 12 }));
  const endDate = dateFilter?.dateFilter?.[1] ?? formatDate(endOfToday());
  // The toolbar's own label is authoritative — passing it through spares the
  // create page a guess it can only make on calendar-day granularity. An
  // incoming window with no label is absolute; the fallback above is ours.
  const datePreset = dateFilter?.dateFilter
    ? (dateFilter?.dateOption ?? "Custom")
    : "12M";

  const values = {
    name: "",
    project: observeId,
    rowType,
    filters,
    spansLimit: 100000,
    samplingRate: 50,
    evalsDetails: [],
    startDate,
    endDate,
    datePreset,
    runType: "historical",
  };

  const draftId = crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  try {
    const storage = globalThis.window?.localStorage ?? globalThis.localStorage;
    storage?.setItem(
      `task-draft-${draftId}`,
      JSON.stringify({ savedAt: Date.now(), values }),
    );
  } catch {
    // localStorage unavailable — page will fall back to defaults
  }
  const params = new URLSearchParams({
    project: observeId,
    draft: draftId,
  });
  if (returnTo) {
    params.set("returnTo", returnTo);
  }
  return `/dashboard/tasks/create?${params.toString()}`;
}
