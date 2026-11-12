// The WORKS slots of madeReal.js, in its order. registryClaims.js re-exports
// them; the ownership helpers it reads take them from here, so no import
// cycle runs through the claim contract.
export const NEW_SLOTS = Object.freeze([
  "XP",
  "RES",
  "IN",
  "ORG",
  "GRID",
  "EVAL",
  "COLLAB",
  "SRC",
]);
