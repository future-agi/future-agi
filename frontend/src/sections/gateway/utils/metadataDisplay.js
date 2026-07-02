import { isGeneratedCamelAlias } from "src/utils/responseAliasMetadata";

const isObject = (value) => Boolean(value && typeof value === "object");

export const normalizeGatewayMetadata = (metadata, seen = new WeakMap()) => {
  if (!isObject(metadata)) return metadata;

  if (seen.has(metadata)) return seen.get(metadata);

  if (Array.isArray(metadata)) {
    const result = [];
    seen.set(metadata, result);
    metadata.forEach((item, index) => {
      result[index] = normalizeGatewayMetadata(item, seen);
    });
    return result;
  }

  const result = Object.create(null);
  seen.set(metadata, result);
  Object.entries(metadata).forEach(([key, value]) => {
    if (isGeneratedCamelAlias(metadata, key)) return;
    Object.defineProperty(result, key, {
      value: normalizeGatewayMetadata(value, seen),
      enumerable: true,
      configurable: true,
      writable: true,
    });
  });
  return result;
};

export const normalizeGatewayMetadataField = (record) => {
  if (!record || typeof record !== "object" || !("metadata" in record)) {
    return record;
  }

  return {
    ...record,
    metadata: normalizeGatewayMetadata(record.metadata),
  };
};

export const normalizeGatewayMetadataResponse = (response) => {
  const normalized = normalizeGatewayMetadataField(response);
  if (!normalized?.result || typeof normalized.result !== "object") {
    return normalized;
  }

  return {
    ...normalized,
    result: normalizeGatewayMetadataField(normalized.result),
  };
};

export const getGatewayMetadataEntries = (metadata) => {
  const normalized = normalizeGatewayMetadata(metadata);
  return isObject(normalized) ? Object.entries(normalized) : [];
};

export const hasGatewayMetadata = (metadata) =>
  metadata !== null &&
  metadata !== undefined &&
  (!isObject(metadata) || getGatewayMetadataEntries(metadata).length > 0);

export const stringifyGatewayMetadata = (metadata, space = 2) => {
  const normalized =
    metadata === null || metadata === undefined
      ? {}
      : normalizeGatewayMetadata(metadata);
  const json = JSON.stringify(normalized, null, space);
  return json === undefined ? String(normalized) : json;
};

export const stringifyGatewayMetadataValue = (value) => {
  const normalized = normalizeGatewayMetadata(value);
  const json = isObject(normalized) ? JSON.stringify(normalized) : undefined;
  return json === undefined ? String(normalized) : json;
};
