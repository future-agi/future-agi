export const CUSTOM_SUMMARY_PREFIX = "custom:";
export const DEFAULT_SUMMARY_TYPE = "concise";

export function resolveSummarySelection(summary) {
  if (summary && typeof summary === "object") {
    const templateId = summary.template_id || summary.templateId;
    if (summary.type === "custom" && templateId) {
      return `${CUSTOM_SUMMARY_PREFIX}${templateId}`;
    }
    if (Object.prototype.hasOwnProperty.call(summary, "type")) {
      return summary.type;
    }
  }
  if (typeof summary === "string" && summary.trim()) return summary;
  return DEFAULT_SUMMARY_TYPE;
}

export function getSummaryTemplateId(selection) {
  if (
    typeof selection === "string" &&
    selection.startsWith(CUSTOM_SUMMARY_PREFIX)
  ) {
    return selection.slice(CUSTOM_SUMMARY_PREFIX.length);
  }
  return null;
}

export function buildSummaryConfig(
  selection,
  { customSummary, templateId } = {},
) {
  if (selection === null) return { type: null };
  const selectedTemplateId = getSummaryTemplateId(selection);
  if (selectedTemplateId) {
    return {
      type: "custom",
      custom: customSummary || "",
      template_id: templateId || selectedTemplateId,
    };
  }
  if (selection === "custom") {
    return { type: "custom", custom: customSummary || "" };
  }
  return { type: selection || DEFAULT_SUMMARY_TYPE };
}
