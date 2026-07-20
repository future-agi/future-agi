export function invalidateEvalDeletionQueries(queryClient, testId, executionIds) {
  queryClient.invalidateQueries({
    queryKey: ["test-runs-detail", testId],
  });

  if (executionIds?.length) {
    executionIds.forEach((executionId) => {
      queryClient.invalidateQueries({
        queryKey: ["test-execution-detail", "KPIS", executionId],
      });
    });
  } else {
    queryClient.invalidateQueries({
      queryKey: ["test-execution-detail", "KPIS"],
    });
  }

  queryClient.invalidateQueries({
    queryKey: ["test-execution-analytics", testId],
  });
}
