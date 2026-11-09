// The host axios instance (src/utils/axios.js) for the browser harness, reduced to
// what sessionAssertion.js depends on: a real axios instance whose rejection has the
// host's shape, the response body spread flat plus `statusCode`, `transportCode`,
// and transport-only `responseHeaders`, with no `response`. The real minter runs
// unchanged against the harness's exchange. sessionAssertion.hostAxios.test.js
// holds this stub to the real instance: the same answers give the same rejection
// and the same Omega error.
import axios from "axios";

/** src/utils/axios.js's response-error interceptor, as it shapes a rejection. */
export function hostRejection(error) {
  const errData = (error.response && error.response.data) || {
    message: error?.message || "Something went wrong",
  };
  return {
    ...errData,
    statusCode: error.response?.status,
    transportCode: error.code,
    // Host intent: 69a61655d6 (via 9119be230c), transport request-id only.
    responseHeaders: {
      "x-request-id": error.response?.headers?.["x-request-id"],
    },
  };
}

const instance = axios.create();
instance.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(hostRejection(error)),
);

export default instance;
