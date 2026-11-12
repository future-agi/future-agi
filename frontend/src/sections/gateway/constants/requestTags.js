import { BILLING_MODE_LABEL } from "../utils/providerLabel";

// Metadata keys a caller sets to name the application and service behind a request.
export const REQUEST_TAG = {
  APPLICATION: "application",
  SERVICE: "service",
};

export const CUSTOM_TAG_SEPARATOR = ":";

// Group-by options shared by the Usage, Cost and Errors tabs. Provider names
// a managed call's group "Future AGI managed";
// Billing mode splits Managed from Own key calls (one name per billing mode;
// the API names the groups).
export const REQUEST_DIMENSION_OPTIONS = [
  { value: "model", label: "Model" },
  { value: "provider", label: "Provider" },
  { value: "billing_mode", label: BILLING_MODE_LABEL },
  { value: REQUEST_TAG.APPLICATION, label: "Application" },
  { value: REQUEST_TAG.SERVICE, label: "Service" },
];
