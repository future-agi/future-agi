// What the property dialog sends. Required and Allowed Values are stored and
// checked by POST /agentcc/custom-properties/validate/. A Default is not
// applied anywhere (the gateway and the request logs never fill it in), so
// the dialog offers none and the API refuses a new one (400
// property_rule_not_enforced): a payload carries default_value only to clear
// a stored default, as null, when the user asked for that. Leaving it out
// keeps a stored default as it is.
export const buildCustomPropertyPayload = (form) => {
  const payload = {
    name: form.name,
    description: form.description,
    property_type: form.property_type,
    required: Boolean(form.required),
    allowed_values:
      form.property_type === "enum" ? [...(form.allowed_values || [])] : [],
  };
  if (form.clearStoredDefault) payload.default_value = null;
  return payload;
};

export const hasStoredDefault = (property) =>
  property?.default_value !== undefined && property?.default_value !== null;
