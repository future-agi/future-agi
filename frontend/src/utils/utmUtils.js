export const isValidUtm = (val) =>
  !!val &&
  val !== "undefined" &&
  val !== "null" &&
  !/^\{\{.*\}\}$/.test(val);
