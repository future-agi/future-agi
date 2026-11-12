const statusOf = (error) => error?.statusCode ?? error?.response?.status;

export const isExperimentReadUnavailable = (error) =>
  [401, 403, 404].includes(statusOf(error));

// A failed observer mounting again must not start another retry cycle. Recovery
// is deliberate (refetch); transient transport/server errors get two retries.
export const experimentReadPolicy = {
  retry: (failureCount, error) =>
    !isExperimentReadUnavailable(error) && failureCount < 2,
  retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  retryOnMount: false,
  refetchOnMount: (query) => !query.state.error,
  refetchOnWindowFocus: (query) => !query.state.error,
  refetchOnReconnect: (query) => !query.state.error,
};
