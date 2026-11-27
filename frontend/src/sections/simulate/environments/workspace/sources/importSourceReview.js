import { paths } from "src/routes/paths";

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
                !clipped(statements.get(proposal.statement_id)) &&
                acceptedRuleIds.includes(proposal.rule_id)
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

// Old previews omitted the flag and may end exactly at the excerpt limit.
const clipped = (statement) =>
  statement?.text_truncated ?? (statement?.text?.length ?? 0) >= 2000;

// A file's first line that only repeats its name or says "X policy" is a title, not a passage.
export function isHeading(source, statement, index, list) {
  if (index !== 0 || list.length < 2) return false;
  const words = (text) =>
    text
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  const [firstLine, ...rest] = statement.text.trim().split(/\r?\n/);
  return (
    words(statement.text) ===
      words((source.filename || "").replace(/\.[^.]+$/, "")) ||
    (firstLine.trim().split(/\s+/).length <= 8 &&
      /^(?:[\p{L}-]+\s+){0,6}(?:policy|policies|guidelines|handbook)$/iu.test(
        firstLine.trim(),
      ) &&
      rest.every(
        (line) =>
          !line.trim() || /^(?:policy\s+)?version\s*:/i.test(line.trim()),
      ))
  );
}

// A rule can be ticked when the server can check it: supported, titled and not clipped.
function checkableRules(preview) {
  const statements = new Map(
    (preview?.sources || []).flatMap((source) =>
      (source.statements || []).map((statement) => [
        statement.statement_id,
        { source, statement },
      ]),
    ),
  );
  return (preview?.proposals?.policy_rules || []).flatMap((rule) => {
    const found = statements.get(rule.statement_id);
    return rule.status === "supported" &&
      rule.title &&
      found?.statement.text &&
      !clipped(found.statement)
      ? [{ rule, ...found }]
      : [];
  });
}

export const checkableRuleIds = (preview) =>
  checkableRules(preview).map(({ rule }) => rule.rule_id);

const pageRef = (source, statement) =>
  [source.filename, statement.page != null && `page ${statement.page}`]
    .filter(Boolean)
    .join(", ");

/**
 * The review as `groupProposals` items: a rule per checkable rule, one
 * background item per text file (its other passages), and a personal data
 * item per file that needs a choice. Records files aren't items: their
 * column decisions stay with the panel.
 */
export function reviewItems(preview, { testedAgent } = {}) {
  const rules = checkableRules(preview);
  const ruled = new Set(rules.map(({ statement }) => statement.statement_id));
  const items = rules.map(({ rule, source, statement }) => ({
    id: rule.rule_id,
    kind: "rule",
    title: rule.title,
    cite: {
      sourceId: source.pin.source_id,
      ref: pageRef(source, statement),
      excerpt: statement.text,
    },
  }));
  const sources = preview?.sources || [];
  for (const source of sources) {
    const { pin, filename, statements } = source;
    // A repair preview carries counts only, with nothing to read yet.
    if (pin?.role === "seed_records" || !statements) continue;
    const passages = statements
      .filter((statement) => statement.text)
      .filter(
        (statement, index, list) => !isHeading(source, statement, index, list),
      )
      .filter((statement) => !ruled.has(statement.statement_id));
    const withheld = statements.some((statement) => !statement.text);
    if (!passages.length && !withheld) continue;
    const pageOf = (statement) => statement.page ?? null;
    items.push({
      id: `${pin.source_id}:reference`,
      kind: "reference",
      title: filename,
      cite: {
        sourceId: pin.source_id,
        ref: filename,
        ...(source.origin?.kind === "knowledge_base" && {
          href: source.origin.kb_id
            ? `${paths.dashboard.knowledge_base}/${encodeURIComponent(source.origin.kb_id)}`
            : paths.dashboard.knowledge_base,
        }),
      },
      role: pin.role,
      testedAgent,
      rules: rules.filter((one) => one.source === source).length,
      read: {
        sections: new Set(passages.map(pageOf)).size,
        words: passages.reduce(
          (sum, { text }) => sum + text.trim().split(/\s+/).length,
          0,
        ),
        passages: passages.map((statement) => ({
          id: statement.statement_id,
          where: statement.page != null ? String(statement.page) : "",
          text: clipped(statement) ? `${statement.text}…` : statement.text,
        })),
      },
      pages: new Set(statements.map(pageOf).filter((page) => page != null))
        .size,
      withheld,
    });
  }
  const personal = new Set([
    ...(preview?.proposals?.field_mappings || [])
      .filter(({ mapping }) => mapping?.personal_data)
      .map(({ source_id }) => source_id),
    ...(preview?.diagnostics || [])
      .filter(
        (d) =>
          d.code === "source_privacy_unresolved" &&
          sources.some(
            (s) =>
              s.pin.source_id === d.span?.source_id &&
              s.pin.role !== "seed_records",
          ),
      )
      .map((d) => d.span.source_id),
  ]);
  for (const source of sources) {
    if (personal.has(source.pin.source_id))
      items.push({
        id: `${source.pin.source_id}:personal`,
        kind: "personal",
        sourceId: source.pin.source_id,
        title: `Personal data in ${source.filename}`,
      });
  }
  return items;
}
