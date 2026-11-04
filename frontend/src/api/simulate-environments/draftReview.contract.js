import { z } from "zod";

// The draft review API as PR #3724's service code returns it, at 19889a1b7.
// Written by hand because the schema generated from its serializers is looser
// and in places wrong: `decision` and the digests can be null, `affects` is a
// list or "all", and patches and sources hold nested values. Replace with the
// generated schema once those serializers are fixed and the PR is merged.
const KINDS = [
  "tool",
  "rule",
  "contract_field",
  "sub_goal",
  "actor",
  "world_state",
  "source",
  "scenario",
  "file",
];
const DECISIONS = ["accepted", "rejected"];

// Kinds a scenario names; anything else has no citing scenario, so touches all.
const NAMED_BY_SCENARIOS = ["tool", "sub_goal", "actor", "scenario"];

const PatchOpSchema = z.object({
  op: z.enum(["test", "add", "remove", "replace", "move", "copy"]),
  path: z.string(),
  value: z.unknown().optional(),
  from: z.string().optional(),
});

// What a write read. A chat write names the person's message; a chat or file
// write can also name a source and where in it.
const UsedSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("message"), id: z.string() }),
  z.object({
    kind: z.literal("source"),
    source_id: z.string(),
    version: z.string().optional(),
    digest: z.string().optional(),
    locator: z.string().optional(),
  }),
]);

const SourceSchema = z.object({
  origin: z.enum(["chat", "manual", "nudge"]),
  agent: z.object({
    kind: z.enum(["user", "harness"]),
    user_id: z.string().optional(),
    on_behalf_of: z.string().optional(),
  }),
  at: z.string(),
  draft_revision: z.number(),
  tool: z.string().optional(),
  used: z.array(UsedSchema).optional(),
  note: z.string().optional(),
});

const RowCountSchema = z.object({
  table: z.string(),
  added: z.number(),
  removed: z.number(),
  changed: z.number(),
});

const ChangeSchema = z
  .object({
    id: z.string().min(1),
    piece: z.enum(["environment", "suite"]),
    kind: z.enum(KINDS),
    key: z.string().min(1),
    document: z.string().min(1),
    action: z.enum(["added", "removed", "changed"]),
    fields: z.array(z.string()),
    patch: z.array(PatchOpSchema),
    base_digest: z.string().nullable(),
    draft_digest: z.string().nullable(),
    summary: z.string().min(1),
    decision: z.enum(DECISIONS).nullable(),
    affects: z.union([z.literal("all"), z.array(z.string())]),
    sources: z.array(SourceSchema),
    depends_on: z.array(z.string()),
    graded: z.boolean(),
    revalidate: z.array(z.string()),
    rows: z.array(RowCountSchema).nullable().optional(),
  })
  .superRefine((change, ctx) => {
    const problem = (message) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, message });
    if (change.depends_on.length && change.kind !== "scenario") {
      problem("Only a scenario depends on other changes");
    }
    if (change.affects === "all" && NAMED_BY_SCENARIOS.includes(change.kind)) {
      problem("A change scenarios name touches a list of them, not all");
    }
    if (change.affects !== "all" && !NAMED_BY_SCENARIOS.includes(change.kind)) {
      problem("A change no scenario names touches all of them");
    }
    if (!change.graded && change.revalidate.length) {
      problem("An ungraded change has nothing to revalidate");
    }
    if (change.kind === "world_state" && change.rows === undefined) {
      problem("A world state change has rows, or null when unreadable");
    }
  });

// Proposed, not in the backend yet: what the failed changes ticket has to return.
const FailedChangeSchema = z.object({
  id: z.string().min(1),
  detail: z.string().min(1),
  reason: z.string().min(1),
  source_id: z.string().nullable(),
  scenarios: z.array(z.string()),
  dismissed: z.boolean().optional(),
});

export const DraftReviewSchema = z
  .object({
    failed: z.array(FailedChangeSchema).optional(),
    drafts: z.record(
      z.enum(["environment", "suite"]),
      z.object({
        id: z.string().min(1),
        revision: z.number(),
        parent_id: z.string(),
      }),
    ),
    changes: z.array(ChangeSchema),
    counts: z.object({
      total: z.number(),
      accepted: z.number(),
      rejected: z.number(),
      undecided: z.number(),
    }),
    review_digest: z.string().min(1),
    can_save: z.boolean(),
  })
  .superRefine((review, ctx) => {
    const added = new Set(
      review.changes
        .filter((one) => one.action === "added")
        .map((one) => one.id),
    );
    review.changes.forEach((change) => {
      const missing = change.depends_on.filter((id) => !added.has(id));
      if (missing.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `A scenario only depends on items the draft adds: ${missing.join(", ")}`,
        });
      }
    });
  });

// POST .../drafts/review/decisions/. An item with no id, kind or remaining
// decides every change.
export const ReviewDecisionSchema = z.object({
  id: z.string().min(1).optional(),
  kind: z.enum(KINDS).optional(),
  remaining: z.boolean().optional(),
  // Null clears a decision, for Restore and Change. Proposed: the backend only
  // takes accepted or rejected so far.
  decision: z.enum(DECISIONS).nullable(),
});

export const ReviewDecisionsBodySchema = z.object({
  expected_revisions: z.record(z.string(), z.number()),
  decisions: z.array(ReviewDecisionSchema),
});

// POST .../drafts/save/ gains these beside `drafts` and `idempotency_key`.
export const ReviewedSaveFieldsSchema = z.object({
  expected_review_digest: z.string().min(1).optional(),
  name: z.string().max(80).optional(),
});

// Refusals by code, with what each carries besides `message`.
export const REVIEW_REFUSALS = Object.freeze({
  draft_not_found: { status: 404 },
  change_not_found: { status: 404, details: ["id"] },
  decision_invalid: { status: 400 },
  draft_changed: { status: 409, details: ["piece"] },
  draft_stale: { status: 409, details: ["piece"] },
  review_stale: { status: 409, details: ["changes"] },
  review_undecided: { status: 409, details: ["changes"] },
  change_depends_on_rejected: { status: 409, details: ["pairs"] },
  idempotency_key_reused: { status: 409 },
  chat_working: { status: 409, details: ["conversation_id"] },
  rebuild_running: { status: 409, details: ["piece"] },
  draft_not_runnable: { status: 422, details: ["piece"] },
  authoring_incomplete: { status: 422 },
});
