import { API_SURFACE_PATHS } from "./api-surface.generated.js";

const PARAM_RE = /\{([^}]+)\}/g;

export const isContractedApiPath = (template) =>
  Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, template);

export const getContractedApiMethods = (template) =>
  API_SURFACE_PATHS[template] || [];

export const apiPath = (template, params = {}) => {
  if (!isContractedApiPath(template)) {
    throw new Error(`API path is not in generated contract: ${template}`);
  }

  return template.replace(PARAM_RE, (_, key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") {
      throw new Error(`Missing API path param "${key}" for ${template}`);
    }
    return encodeURIComponent(String(value));
  });
};
