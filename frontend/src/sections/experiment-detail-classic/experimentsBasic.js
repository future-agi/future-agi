import { isPrototypeMode } from "src/utils/prototype";
import * as launchSwitch from "src/launch/launchSwitch";

// Prototype builds and builds that show unreleased parts use DSX readers.
// Other hosted/self-host builds keep classic pages regardless of experimentsBasic.
// Selecting DSX readers does not enable prototype mode or mock data.
export const EXPERIMENTS_BASIC_ENV_KEY = "VITE_DSX_EXPERIMENTS_BASIC";

export const experimentsBasicFromEnv = launchSwitch.experimentsBasicFromEnv;

export function isExperimentsBasicOn() {
  return launchSwitch.experimentsBasicOn();
}

export function experimentSurfaceOf({ prototype, showUnreleased = false }) {
  // Classic capabilities still do not depend on experimentsBasic.
  return prototype || showUnreleased ? "dsx" : "classic";
}

export function useExperimentSurface() {
  return experimentSurfaceOf({
    prototype: isPrototypeMode(),
    // Read the switch optionally so the same selector works on a build
    // whose launch module does not export it.
    showUnreleased:
      "SHOW_UNRELEASED_PARTS" in launchSwitch &&
      Reflect.get(launchSwitch, "SHOW_UNRELEASED_PARTS") === true,
  });
}
