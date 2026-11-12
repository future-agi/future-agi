/**
 * Who did an Activity entry, in words. EE records the actor as its internal
 * account id (`acc_…`) for a person and as `system:<what>` for automatic work
 * such as the idle reaper. Neither may reach a customer screen (COPY1, the
 * console-reality audit's F17), and EE's sandbox API names no person yet, so a
 * person reads as "Workspace member" until the Activity API returns names
 * (SB:W2-OBSERVE), and automatic work as "Automatic" ("Automatic (idle)" for
 * the idle hibernation).
 */
export function actorWords(actor) {
  if (typeof actor !== "string" || actor === "") return actor;
  if (actor.startsWith("system:"))
    return /idle|reap/i.test(actor) ? "Automatic (idle)" : "Automatic";
  return "Workspace member";
}

/** Activity lines with the actor in words; the raw id never reaches the page. */
export function withActorWords(lines) {
  if (!Array.isArray(lines)) return lines;
  return lines.map((line) => ({ ...line, actor: actorWords(line.actor) }));
}
