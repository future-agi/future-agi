// A dataset id that does not exist (layout #1, options-truth L2 G7): a page
// that says so and leads back to Datasets, instead of a grid of ERR cells.
import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { Helmet } from "react-helmet-async";
import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import EmptyState from "src/sections/common/ux/EmptyState";

/**
 * The HTTP status of a failed request (pure). The app's axios rejects with
 * `{status: false, result, statusCode}`; a raw axios error has
 * `response.status`.
 */
export const statusOfError = (error) => {
  const n = [error?.statusCode, error?.response?.status, error?.status]
    .map(Number)
    .find((x) => Number.isInteger(x) && x >= 100);
  return n ?? null;
};

/**
 * PF-3 (PUX-5): dev's get-dataset-table answers a missing (or another
 * org's) dataset with 400 DATASET_NOT_FOUND, not 404; the prototype mock
 * answers 404. The message is pinned to tfc/utils/error_codes.py.
 */
export const DATASET_NOT_FOUND_MESSAGE =
  "The specified dataset does not exist or has been deleted.";
const NOT_FOUND_CODES = new Set(["dataset_not_found", "DATASET_NOT_FOUND"]);

/** Whether a failed dataset read says the dataset does not exist (pure). */
export function isDatasetGoneError(error) {
  const status = statusOfError(error);
  if (status === 404) return true;
  if (status !== 400) return false;
  if (NOT_FOUND_CODES.has(String(error?.code ?? ""))) return true;
  return [error?.detail, error?.message, error?.error, error?.result].some(
    (t) => typeof t === "string" && t.trim() === DATASET_NOT_FOUND_MESSAGE,
  );
}

/**
 * True once a dataset id is known not to exist: the dataset list loaded
 * without it AND the dataset's own read answers not-found (404, or dev's
 * 400 DATASET_NOT_FOUND) after a few looks (a deep link loaded under load
 * can ask before the data is ready, so one miss is not enough). No extra
 * request while the list has the dataset.
 */
export function useDatasetAvailability(datasetId, { list, listLoading }) {
  const inList = (list ?? []).some((d) => d.datasetId === datasetId);
  const enabled = Boolean(datasetId) && !listLoading && !inList;
  const q = useQuery({
    queryKey: ["dataset-exists", datasetId],
    enabled,
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { column_config_only: true },
      }),
    retry: (count, error) => isDatasetGoneError(error) && count < 3,
    retryDelay: (attempt) => 400 * 2 ** attempt,
    staleTime: 30000,
    meta: { errorHandled: true },
  });
  return {
    pending:
      Boolean(datasetId) &&
      !inList &&
      (listLoading || (enabled && q.isPending)),
    missing:
      enabled &&
      q.isError &&
      (isDatasetGoneError(q.error) || statusOfError(q.error) === 403),
    error: enabled && q.isError ? q.error : null,
    refetch: q.refetch,
  };
}

export function useDatasetMissing(datasetId, options) {
  return useDatasetAvailability(datasetId, options).missing;
}

export function DatasetReadError({ error, onRetry }) {
  if (isDatasetGoneError(error) || statusOfError(error) === 403)
    return <DatasetNotFound />;
  return (
    <Box role="alert" sx={{ p: 3 }}>
      <EmptyState
        title="Could not load dataset"
        body="The dataset could not be read. Retry, or return to Datasets."
        actions={[
          { label: "Retry", onClick: onRetry },
          { label: "Back to Datasets", href: paths.dashboard.develop },
        ]}
      />
    </Box>
  );
}

DatasetReadError.propTypes = {
  error: PropTypes.object,
  onRetry: PropTypes.func,
};

export default function DatasetNotFound() {
  return (
    <Box
      data-testid="dataset-not-found"
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: "background.paper",
        p: 3,
      }}
    >
      <Helmet>
        <title>Dataset not found · Future AGI</title>
      </Helmet>
      <EmptyState
        icon="mdi:database-off-outline"
        title="Dataset not found"
        body="This dataset is unavailable. Check the link and your workspace access, or return to Datasets."
        actions={[
          {
            label: "Back to Datasets",
            href: paths.dashboard.develop,
            icon: "mdi:arrow-left",
          },
        ]}
      />
    </Box>
  );
}
