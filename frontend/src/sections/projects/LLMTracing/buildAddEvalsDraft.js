import { endOfToday, sub } from "date-fns";
import { formatDate } from "src/utils/report-utils";
import { getRandomId } from "src/utils/utils";

const COL_TYPE_TO_CATEGORY = {
  SPAN_ATTRIBUTE: "attribute",
  SYSTEM_METRIC: "system",
  EVALUATION_METRIC: "eval",
  ANNOTATION: "annotation",
};

const toFormRows = (sourceFilters = []) => {
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
        fieldCategory: category,
        fieldLabel: field,
        filterConfig: {
          filterType: cfg.filter_type === "number" ? "number" : "text",
          filterOp: cfg.filter_op || "equals",
          filterValue: v,
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
  const filters = [...toFormRows(mainFilters), ...toFormRows(extraFilters)];
  const startDate =
    dateFilter?.dateFilter?.[0] ?? formatDate(sub(new Date(), { months: 12 }));
  const endDate = dateFilter?.dateFilter?.[1] ?? formatDate(endOfToday());

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
    runType: "historical",
  };

  const draftId = crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  try {
    localStorage.setItem(
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
