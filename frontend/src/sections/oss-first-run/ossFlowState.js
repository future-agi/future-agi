// Whether the OSS first-run checks have been completed.
//
// Lives in localStorage by decision: re-running the checks is idempotent and
// read-only, so replaying setup is an annoyance rather than damage. This holds
// ONLY while checks stay read-only — if a check ever writes config, creates a
// bucket, or runs a migration, completion has to move server-side.
//
// There is deliberately no "account created" flag. Routing never chooses
// between login and signup: once the checks are done, an unauthenticated
// visitor goes to login like everywhere else, and signup is reached only by
// finishing the checks.

const VALIDATION_DONE = "oss_validation_done";

const read = (key) => {
  try {
    return localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
};

const write = (key) => {
  try {
    localStorage.setItem(key, "true");
  } catch {
    /* private mode / storage disabled — flow still works, it just replays */
  }
};

export const isValidationDone = () => read(VALIDATION_DONE);
export const markValidationDone = () => write(VALIDATION_DONE);
