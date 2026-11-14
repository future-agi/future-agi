// Metadata keys a caller sets to name the application and service behind a request.
export const REQUEST_TAG = {
  APPLICATION: "application",
  SERVICE: "service",
};

export const CUSTOM_TAG_SEPARATOR = ":";

// Group-by options shared by the Usage, Cost and Errors tabs.
export const REQUEST_DIMENSION_OPTIONS = [
  { value: "model", label: "Model" },
  { value: "provider", label: "Provider" },
  { value: REQUEST_TAG.APPLICATION, label: "Application" },
  { value: REQUEST_TAG.SERVICE, label: "Service" },
];
