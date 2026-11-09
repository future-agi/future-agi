const COLUMN_MUTATION_TIMEOUT_MS = 15_000;

/** Bound the whole Axios promise, including auth refresh and queue waiting. */
export async function runColumnMutation(request) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_resolve, reject) => {
    timer = setTimeout(() => {
      const error = new Error("Column mutation timed out");
      error.transportCode = "ECONNABORTED";
      reject(error);
      // Do not cancel the shared refresh. Abort this request's signal so a
      // late refresh cannot replay the expired mutation behind the dialog.
      controller.abort();
    }, COLUMN_MUTATION_TIMEOUT_MS);
  });

  try {
    return await Promise.race([
      request({
        timeout: COLUMN_MUTATION_TIMEOUT_MS,
        signal: controller.signal,
      }),
      deadline,
    ]);
  } finally {
    clearTimeout(timer);
  }
}
