const PROCESSING_MESSAGE =
  "Rows are still being added in the background. This can take a while for large selections.";

export function getAddToDatasetStatus(result, completedMessage) {
  const isProcessing = result?.status !== "completed";

  return {
    message: isProcessing ? PROCESSING_MESSAGE : completedMessage,
    variant: isProcessing ? "info" : "success",
  };
}
