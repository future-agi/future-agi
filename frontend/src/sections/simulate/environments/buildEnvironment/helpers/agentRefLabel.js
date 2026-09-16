// A short human label for the agent a draft points at, shown in the read-audit
// header and streamed into the builder narration. Ported from the designer's
// agentRefLabel (BuildFromAgent.jsx:599–607), adapted to the redacted Phase-1
// draft shape (repo/platform/upload).
export function agentRefLabel(draft) {
  if (!draft) return "agent";
  if (draft.kind === "repo") {
    if (!draft.value) return "agent";
    return draft.ref ? `${draft.value}@${draft.ref}` : draft.value;
  }
  if (draft.kind === "platform") return `${draft.provider} · ${draft.agentId}`;
  if (draft.kind === "upload") return draft.entry || draft.files?.[0]?.name || "agent";
  return "agent";
}
