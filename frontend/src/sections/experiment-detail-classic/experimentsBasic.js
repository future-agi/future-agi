import { isPrototypeMode } from "src/utils/prototype";
import {
  experimentsBasicFromEnv as launchExperimentsBasicFromEnv,
  experimentsBasicOn,
} from "src/launch/launchSwitch";

// J09 REVISED / 767: prototype builds keep DSX; hosted and self-host builds
// keep the classic experiment pages/list even when experimentsBasic is OFF.
// The launch flag still gates NEW surfaces in launchSurfaces.js. It does not
// activate the DSX Results wrapper, leaderboard, compare or analysis here.
export const EXPERIMENTS_BASIC_ENV_KEY = "VITE_DSX_EXPERIMENTS_BASIC";

export const experimentsBasicFromEnv = launchExperimentsBasicFromEnv;

export function isExperimentsBasicOn() {
  return experimentsBasicOn();
}

export function experimentSurfaceOf({ prototype }) {
  // J09 REVISED: classic capabilities do not depend on experimentsBasic.
  return prototype ? "dsx" : "classic";
}

export function useExperimentSurface() {
  return experimentSurfaceOf({
    prototype: isPrototypeMode(),
  });
}
