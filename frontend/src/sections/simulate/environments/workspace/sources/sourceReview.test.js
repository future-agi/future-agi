import { describe, expect, it } from "vitest";
import { buildSourceReview } from "./sourceReview";

const source = (sourceId, role, extra = {}) => ({
  pin: { source_id: sourceId, role },
  ...extra,
});

describe("buildSourceReview", () => {
  it("keeps every decision tied to its source and applies safe defaults", () => {
    const personalMapping = {
      source_id: "customers",
      mapping: {
        source_field: "email",
        target_field: "email",
        personal_data: true,
      },
    };
    const supportedRule = { statement_id: "rule-1", status: "supported" };
    const unsupportedRule = { statement_id: "rule-2", status: "unsupported" };
    const relationship = {
      from_source_id: "orders",
      to_source_id: "customers",
    };

    expect(
      buildSourceReview(
        {
          draft_digest: "sha256:draft",
          sources: [
            source("customers", "seed_records"),
            source("policy", "policy", {
              statements: [
                { statement_id: "rule-1" },
                { statement_id: "rule-2" },
              ],
            }),
            source("guide", "reference"),
          ],
          proposals: {
            field_mappings: [personalMapping],
            policy_rules: [supportedRule, unsupportedRule],
            relationships: [{ relationship }],
          },
        },
        {
          acceptedMappingIndexes: [0],
          acceptedRuleIds: ["rule-1"],
          acceptedRelationshipIndexes: [0],
          personalDataModes: { customers: "synthetic_replace" },
        },
      ),
    ).toEqual({
      expected_draft_digest: "sha256:draft",
      record_reviews: [
        {
          source_id: "customers",
          audience: { tested_agent: false, participant_ids: [] },
          personal_data_mode: "synthetic_replace",
          fields: [personalMapping.mapping],
        },
      ],
      policy_reviews: [
        {
          source_id: "policy",
          audience: { tested_agent: false, participant_ids: [] },
          rules: [
            { proposal: supportedRule, decision: "accepted" },
            { proposal: unsupportedRule, decision: "reference_only" },
          ],
        },
      ],
      reference_reviews: [
        {
          source_id: "guide",
          audience: { tested_agent: false, participant_ids: [] },
        },
      ],
      relationships: [relationship],
    });
  });

  it("can expose reviewed content to the tested agent", () => {
    const review = buildSourceReview(
      { draft_digest: "sha256:draft", sources: [source("guide", "reference")] },
      { testedAgent: true },
    );

    expect(review.reference_reviews[0].audience).toEqual({
      tested_agent: true,
      participant_ids: [],
    });
  });

  it("does not execute proposed rules, mappings, or joins without review", () => {
    const supportedRule = { statement_id: "rule-1", status: "supported" };
    const review = buildSourceReview({
      draft_digest: "sha256:draft",
      sources: [
        source("customers", "seed_records"),
        source("policy", "policy", {
          statements: [{ statement_id: "rule-1" }],
        }),
      ],
      proposals: {
        field_mappings: [
          {
            source_id: "customers",
            mapping: { source_field: "id", target_field: "customer_id" },
          },
        ],
        policy_rules: [supportedRule],
        relationships: [{ relationship: { from_source_id: "orders" } }],
      },
    });

    expect(review.record_reviews[0].fields).toEqual([]);
    expect(review.policy_reviews[0].rules).toEqual([
      { proposal: supportedRule, decision: "reference_only" },
    ]);
    expect(review.relationships).toEqual([]);
  });
});
