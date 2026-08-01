import { API_SURFACE_PATHS } from "./api-surface.generated.js";
import { API_CONTRACT_EXCEPTIONS } from "./api-contract-exceptions.js";

const PARAM_RE = /\{([^}]+)\}/g;

export const isContractedApiPath = (template) =>
  Object.prototype.hasOwnProperty.call(API_SURFACE_PATHS, template);

export const getContractedApiMethods = (template) =>
  API_SURFACE_PATHS[template] || [];

export const isApiContractExceptionPath = (template) =>
  Object.prototype.hasOwnProperty.call(API_CONTRACT_EXCEPTIONS, template);

export const getApiContractExceptionMeta = (template) =>
  API_CONTRACT_EXCEPTIONS[template];

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

export const uncontractedApiPath = (template, params = {}) => {
  if (typeof params === "string") {
    throw new Error(
      `Uncontracted API path metadata belongs in api-contract-exceptions.js: ${template}`,
    );
  }

  if (!isApiContractExceptionPath(template)) {
    throw new Error(
      `API contract exception path is not registered: ${template}`,
    );
  }

  return template.replace(PARAM_RE, (_, key) => {
    const value = params[key];
    if (value === undefined || value === null || value === "") {
      throw new Error(
        `Missing uncontracted API path param "${key}" for ${template}`,
      );
    }
    return encodeURIComponent(String(value));
  });
};
