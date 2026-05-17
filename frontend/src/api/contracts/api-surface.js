import { API_SURFACE_PATHS } from "./api-surface.generated.js";
import { LEGACY_API_SURFACE } from "./legacy-api-surface.js";

const PARAM_RE = /\{([^}]+)\}/g;

export const isContractedApiPath = (template) =>
  Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, template);

export const getContractedApiMethods = (template) =>
  API_SURFACE_PATHS[template] || [];

export const isLegacyApiPath = (template) =>
  Object.prototype.hasOwnProperty.call(LEGACY_API_SURFACE, template);

export const getLegacyApiPathMeta = (template) => LEGACY_API_SURFACE[template];

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

export const legacyApiPath = (template, params = {}) => {
  if (typeof params === "string") {
    throw new Error(
      `Legacy API path metadata belongs in legacy-api-surface.js: ${template}`,
    );
  }

  if (!isLegacyApiPath(template)) {
    throw new Error(`Legacy API path is not registered: ${template}`);
  }

  return template.replace(PARAM_RE, (_, key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") {
      throw new Error(`Missing legacy API path param "${key}" for ${template}`);
    }
    return encodeURIComponent(String(value));
  });
};
