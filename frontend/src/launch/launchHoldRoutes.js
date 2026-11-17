// A published contract alone does not activate Results in launch. Main
// accepted DSX-WALK-RES-21 (Results grid + copies) and DSX-WALK-RES-51
// (analysis + the hosts' runs read), releasing exactly their three GET holds.
// Other prototype paths still require their own MADE_REAL_PATHS claims.
// Explicit holds always win over backing claims in absentPathGuard.
export const LAUNCH_HOLD_ROUTES = Object.freeze([]);
