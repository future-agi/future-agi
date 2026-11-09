/** Axios' interceptor rejects normalized bodies, not raw AxiosError objects. */
export function operationStatus(error) {
  return error?.statusCode ?? error?.response?.status ?? error?.status;
}

/** Only a first-attempt rejection proves that no operation was admitted.
 * A denial while recovering an unknown send cannot disprove its acceptance.
 */
export function isDefiniteRejection(error) {
  const status = operationStatus(error);
  return status >= 400 && status < 500 && ![408, 425, 429].includes(status);
}
