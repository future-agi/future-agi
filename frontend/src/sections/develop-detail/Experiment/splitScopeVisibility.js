import { isSurfaceShown } from "src/launch/launchSwitch";

/** Split execution is independent of organizing dataset splits. */
export const splitScopeShown = () => isSurfaceShown("exp.new.splitScope");
