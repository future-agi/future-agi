// The largest seed a run takes: the platform draws seeds up to 2^63 - 1, past what a JavaScript number holds exactly,
// so a seed stays decimal text from the API to the request.
const MAX_RUN_SEED = 2n ** 63n - 1n;

/**
 * A typed seed as the run request sends it: `{ value }` is the seed's decimal text, null when left empty (the run then
 * keeps the environment's seed); `invalid` when it is not a whole number from 0 to 2^63 - 1.
 */
export function parseRunSeed(text) {
  const typed = (text ?? "").trim();
  if (typed === "") return { value: null, invalid: false };
  if (!/^\d+$/.test(typed) || BigInt(typed) > MAX_RUN_SEED) {
    return { value: null, invalid: true };
  }
  return { value: BigInt(typed).toString(), invalid: false };
}
