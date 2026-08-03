export const getDefaultValueError = ({
  property_type,
  allowed_values,
  default_value,
}) => {
  if (default_value === "") return "";
  const textValue = String(default_value).trim();
  if (property_type === "number" && Number.isNaN(Number(textValue))) {
    return "Default must be a number";
  }
  if (
    property_type === "boolean" &&
    !["true", "false"].includes(textValue.toLowerCase())
  ) {
    return "Default must be true or false";
  }
  if (property_type === "enum" && !allowed_values.includes(textValue)) {
    return "Default must match an allowed value";
  }
  return "";
};

export const buildCustomPropertyPayload = (form) => {
  const payload = { ...form };
  if (payload.property_type !== "enum") {
    payload.allowed_values = [];
  }
  if (payload.default_value === "") {
    payload.default_value = null;
  } else if (payload.property_type === "number") {
    payload.default_value = Number(String(payload.default_value).trim());
  } else if (payload.property_type === "boolean") {
    payload.default_value =
      String(payload.default_value).trim().toLowerCase() === "true";
  } else if (payload.property_type === "enum") {
    payload.default_value = String(payload.default_value).trim();
  }
  return payload;
};
