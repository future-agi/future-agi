// Whether this server serves the side-by-side task read (the task payload and
// its review state), decided in one place for every door that asks for it:
// the drawer's Trajectory marks and the side-by-side page with its links.
// Read from the generated server contract, like the other server-capability
// checks. The prototype world serves it; unit tests are not a real backend
// (realBackendCaps.js), and a test reads one with setRealBackendForTests(true).
import { isContractedApiPath } from "src/api/contracts/api-surface";
import { isRealBackend } from "src/sections/develop/realBackendCaps";

/** The API path template of the side-by-side task read. */
export const TASK_COMPARE_PATH =
  "/model-hub/datasets/{dataset_id}/task-compare/";

/** True where the side-by-side task read is served. */
export const taskCompareServed = (real = isRealBackend()) =>
  !real || isContractedApiPath(TASK_COMPARE_PATH);
