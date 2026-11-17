// HO results R4 (W4-3): a reviewer's corrected output, as R3 returns it on a
// result (`corrected_output {value, by: {id, name}, at}`), for the grid
// chip and the compare tab. Pure.
import { fmtDateShort } from "src/sections/common/format/fmt";

/** The result's corrected output, or null (an empty correction is none). */
export function correctedOf(result) {
  const c = result?.corrected_output;
  if (!c || typeof c.value !== "string" || !c.value.trim()) return null;
  return c;
}

/** `Corrected by Alice Park · Oct 23` (the date when the server sent one). */
export function correctedByLine(corrected, { now } = {}) {
  if (!corrected) return "";
  const who = corrected.by?.name
    ? `Corrected by ${corrected.by.name}`
    : "Corrected";
  const when = corrected.at ? fmtDateShort(corrected.at, { now }) : null;
  return when ? `${who} · ${when}` : who;
}

/** True when the correction says something the output did not. */
export function correctionDiffers(corrected, output) {
  if (!corrected) return false;
  const a = String(corrected.value ?? "").trim();
  const b =
    typeof output === "string" ? output.trim() : JSON.stringify(output ?? "");
  return a !== b;
}

/** The runs of a row that carry a corrected output, in the given order. */
export function correctedRunsOf(row, runs) {
  return (runs ?? [])
    .map((run) => ({ run, corrected: correctedOf(row?.results?.[run.run_id]) }))
    .filter((x) => x.corrected);
}
