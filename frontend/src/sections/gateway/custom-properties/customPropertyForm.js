// What the property dialog sends. Required and Allowed Values are stored and
// checked by POST /agentcc/custom-properties/validate/. A Default is not
// applied anywhere (the gateway and the request logs never fill it in), so
// the dialog offers none and the API refuses a new one (400
// property_rule_not_enforced): a payload carries default_value only to clear
// a default stored before, as null, when the user asked for that. Leaving it
// out keeps a stored default as it is. An edit carries the property's id.
export const buildCustomPropertyPayload = (form, id) => {
  const payload = {
    ...(id && { id }),
    name: form.name.trim(),
    description: form.description,
    property_type: form.property_type,
    required: Boolean(form.required),
    allowed_values:
      form.property_type === "enum" ? [...(form.allowed_values || [])] : [],
  };
  if (form.clearStoredDefault) payload.default_value = null;
  return payload;
};

// Where Required and Allowed values work: the Validate API checks a
// request's properties against them; the gateway does not apply them.
export const RULES_CHECKED_BY_VALIDATE_API =
  "Required and Allowed values are checked by the Validate API (POST /agentcc/custom-properties/validate/); they are not applied by the gateway to requests.";

export const hasStoredDefault = (property) =>
  property?.default_value !== undefined && property?.default_value !== null;

/**
 * Why the server would refuse a save that sends `type` while this stored
 * default is kept, or "". The API checks a stored default against the type
 * a PATCH sends (agentcc/serializers/custom_property.py validate and
 * _default_value_error); removing the stored default lifts it. A list type
 * is checked against the values the form sends, so it is not refused here.
 */
export function typeRefusal(type, storedDefault) {
  if (storedDefault === null || storedDefault === undefined) return "";
  const shown = JSON.stringify(storedDefault);
  if (type === "string" && typeof storedDefault !== "string") {
    return `The stored default ${shown} is not text`;
  }
  if (type === "number" && typeof storedDefault !== "number") {
    return `The stored default ${shown} is not a number`;
  }
  if (type === "boolean" && typeof storedDefault !== "boolean") {
    return `The stored default ${shown} is not true or false`;
  }
  return "";
}
