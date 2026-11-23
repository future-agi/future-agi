/**
 * Composer scaffold — skill chips pinned inside the builder chat's
 * input area (Claude-style).
 *
 * A caller emits a scaffold; the AssistantConsole subscribes and
 * pins a compact skill pill above its text field. Each scaffold is
 * `{ label, prompt, icon?, placeholder? }`: the label is what the pill
 * shows, the prompt is what actually gets prepended to the outgoing
 * message on send, and the placeholder (optional) replaces the input's
 * hint while the pill is pinned — so a skill can say what to type next.
 * `draft` (optional) writes a request into an empty input, ready to send.
 * Legacy callers can still pass a bare string — that string is used for
 * both label and prompt.
 */

const listeners = new Set();

export function subscribeComposerScaffold(fn) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function injectComposerScaffold(input) {
  const scaffold = normalize(input);
  if (!scaffold) return;
  listeners.forEach((fn) => {
    try { fn(scaffold); } catch { /* no-op */ }
  });
}

function normalize(input) {
  if (typeof input === "string") {
    const clean = input.trim();
    if (!clean) return null;
    return { label: clean, prompt: clean, icon: null };
  }
  if (input && typeof input === "object") {
    const label = (input.label || "").toString().trim();
    const prompt = (input.prompt || input.label || "").toString().trim();
    if (!label || !prompt) return null;
    return {
      label,
      prompt,
      icon: input.icon || null,
      placeholder: input.placeholder || null,
      /* Text written into the input for the person to send or edit. */
      draft: input.draft || null,
    };
  }
  return null;
}
