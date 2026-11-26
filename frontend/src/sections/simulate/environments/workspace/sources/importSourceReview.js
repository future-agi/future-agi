export function readableField(name) {
  const short = String(name ?? "")
    .split("/")
    .at(-1);
  try {
    return decodeURIComponent(short);
  } catch {
    return short;
  }
}

export const buildSourceReview = (draft, decisions = {}) => {
  const sources = draft?.sources || [];
  const proposals = draft?.proposals || {};
  const {
    testedAgent = true,
    acceptedMappingIndexes = [],
    stableMappingIndexes,
    acceptedRuleIds = [],
    acceptedRelationshipIndexes = [],
    personalDataModes = {},
  } = decisions;
  const audience = { tested_agent: testedAgent, participant_ids: [] };

  return {
    expected_draft_digest: draft.draft_digest,
    record_reviews: sources
      .filter((source) => source.pin?.role === "seed_records")
      .map((source) => {
        const fields = (proposals.field_mappings || [])
          .map((proposal, index) => ({ proposal, index }))
          .filter(
            ({ proposal, index }) =>
              proposal.source_id === source.pin.source_id &&
              acceptedMappingIndexes.includes(index),
          )
          .map(({ proposal, index }) =>
            stableMappingIndexes
              ? {
                  ...proposal.mapping,
                  is_stable_id: stableMappingIndexes.includes(index),
                }
              : proposal.mapping,
          );
        return {
          source_id: source.pin.source_id,
          audience,
          personal_data_mode: personalDataModes[source.pin.source_id] || "none",
          fields,
        };
      }),
    policy_reviews: sources
      .filter((source) => source.pin?.role === "policy")
      .map((source) => {
        const statements = new Map(
          (source.statements || []).map((statement) => [
            statement.statement_id,
            statement,
          ]),
        );
        return {
          source_id: source.pin.source_id,
          audience,
          rules: (proposals.policy_rules || [])
            .filter((proposal) => statements.has(proposal.statement_id))
            .map((proposal) => ({
              proposal,
              decision:
                proposal.status === "supported" &&
                !statements.get(proposal.statement_id)?.text_truncated &&
                acceptedRuleIds.includes(proposal.statement_id)
                  ? "accepted"
                  : "reference_only",
            })),
        };
      }),
    reference_reviews: sources
      .filter((source) => source.pin?.role === "reference")
      .map((source) => ({ source_id: source.pin.source_id, audience })),
    relationships: (proposals.relationships || [])
      .filter((_, index) => acceptedRelationshipIndexes.includes(index))
      .map((proposal) => proposal.relationship),
  };
};
