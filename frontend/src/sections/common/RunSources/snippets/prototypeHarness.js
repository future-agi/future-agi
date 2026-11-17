// Public entry point: generated runners are available only in local fixtures.
import * as preview from "./harness";
export { excludedToolsOf } from "./harnessTools";

const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";
const INCLUDE_PREVIEW = PROTOTYPE_BUILD || import.meta.env.MODE === "test";

const unavailable = () => {
  throw new Error("Harness runner generation is unavailable in this build.");
};

export const HARNESS_FILE = INCLUDE_PREVIEW ? preview.HARNESS_FILE : null;
export const HARNESS_PACKAGES = INCLUDE_PREVIEW
  ? preview.HARNESS_PACKAGES
  : null;
export const UPLOAD_LINE = INCLUDE_PREVIEW ? preview.UPLOAD_LINE : null;
export const harnessTryCommand = INCLUDE_PREVIEW
  ? preview.harnessTryCommand
  : unavailable;
export const harnessScript = INCLUDE_PREVIEW
  ? preview.harnessScript
  : unavailable;
export const steps = INCLUDE_PREVIEW ? preview.steps : unavailable;
