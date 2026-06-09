/**
 * Utility functions for managing React Query cache
 */

import { isCancelledError } from "@tanstack/react-query";

import logger from "./logger";

// invalidate/refetch return Promises that reject with a cancellation error
// whenever react-query supersedes an in-flight fetch for the same key. These
// callers are intentionally fire-and-forget, so we drop those rejections on
// the floor and only log real failures.
//
// react-query's CancelledError extends Error but never sets `this.name`, so
// `err.name` is "Error" in production and `err.constructor.name` gets mangled
// by the minifier — only `isCancelledError` (instanceof check) is reliable.
// Axios sets `name = "CanceledError"` itself so the name check works there.
const swallowCancellations = (label) => (error) => {
  const name = error?.name;
  const isCancelled =
    isCancelledError(error) ||
    name === "CanceledError" ||
    name === "AbortError" ||
    error?.code === "ERR_CANCELED";
  if (isCancelled) return;
  logger.error(label, error);
};

/**
 * Invalidates and refetches dataset list cache
 * @param {QueryClient} queryClient - React Query client instance
 */
export const invalidateDatasetListCache = (queryClient) => {
  if (!queryClient) {
    logger.warn("QueryClient not provided to invalidateDatasetListCache");
    return;
  }

  const onError = swallowCancellations("Error invalidating dataset cache:");

  queryClient
    .invalidateQueries({ queryKey: ["develop", "dataset-name-list"] })
    .catch(onError);
  queryClient
    .invalidateQueries({ queryKey: ["develop", "dataset-list"] })
    .catch(onError);
  queryClient
    .refetchQueries({ queryKey: ["develop", "dataset-name-list"] })
    .catch(onError);
};

/**
 * Invalidates experiment-related cache
 * @param {QueryClient} queryClient - React Query client instance
 * @param {string} datasetId - Optional dataset ID for specific invalidation
 */
export const invalidateExperimentCache = (queryClient, datasetId = null) => {
  if (!queryClient) {
    logger.warn("QueryClient not provided to invalidateExperimentCache");
    return;
  }

  const onError = swallowCancellations("Error invalidating experiment cache:");

  queryClient
    .invalidateQueries({ queryKey: ["experiments"] })
    .catch(onError);
  if (datasetId) {
    queryClient
      .invalidateQueries({ queryKey: ["experiments", datasetId] })
      .catch(onError);
  }
};
