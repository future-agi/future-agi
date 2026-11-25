export const buildSourceReview = (draft, decisions = {}) => {
  const sources = draft?.sources || [];
  const proposals = draft?.proposals || {};
  const {
    testedAgent = true,
    acceptedMappingIndexes = [],
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
          .map(({ proposal }) => proposal)
          .map((proposal) => proposal.mapping);
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
        const statements = new Set(
          (source.statements || []).map((statement) => statement.statement_id),
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
