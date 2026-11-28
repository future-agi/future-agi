// The REST client normalizes Axios failures before passing them to callers.
export const isTransientRefreshError = (error) => {
  const status = error?.statusCode ?? error?.response?.status;
  return status == null || status === 408 || status === 429 || status >= 500;
};
