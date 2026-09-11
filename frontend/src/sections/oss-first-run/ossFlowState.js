// localStorage is safe only while the checks stay read-only. If one ever writes
// config or runs a migration, completion has to move server-side.

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
