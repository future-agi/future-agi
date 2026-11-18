import { z } from "zod";

// The environment sources API as PR #4158's serializers and service return
// it, by hand because its serializers type most of it as plain JSON. Fields
// under `ui` aren't sent by the backend yet (see UI_ONLY_FEATURES).
const ROLES = ["policy", "reference", "seed_records", "evaluation_reference"];
const STATUSES = [
  "reading",
  "ready",
  "rows_rejected",
  "unreadable",
  "unsupported",
  "not_saved",
];

const DraftRefSchema = z.object({
  id: z.string().nullable(),
  revision: z.number().int().min(0),
});

const RouteLimitsSchema = z.object({
  enabled: z.boolean(),
  formats: z.array(z.string().min(1)),
  max_files: z.number().int().positive(),
  max_file_bytes: z.number().int().positive(),
  max_total_bytes: z.number().int().positive(),
});

export const SourceCapabilitiesSchema = z.object({
  upload: RouteLimitsSchema,
  knowledge_base: RouteLimitsSchema,
  environment_version_id: z.string().min(1),
  draft: DraftRefSchema,
  participant_visibility_enabled: z.boolean(),
  participant_visibility_reason: z.string(),
});

// #4158's summary sends `{source_id, purpose}`; the rest is the UI's view.
const SourceSchema = z.object({
  source_id: z.string().min(1),
  purpose: z.enum(["policy", "reference", "seed_records"]).nullable(),
  name: z.string().min(1),
  ext: z.string(),
  size: z.number().min(0),
  role: z.enum(ROLES).nullable(),
  visibility: z.enum(["agent", "tool", "evaluator"]).nullable(),
  status: z.enum(STATUSES),
  status_note: z.string().nullable(),
  version: z.number().int().positive(),
  origin: z.discriminatedUnion("kind", [
    z.object({ kind: z.literal("upload") }),
    z.object({
      kind: z.literal("knowledge_base"),
      kb_id: z.string().min(1),
      file_id: z.string().min(1),
      collection: z.string(),
    }),
  ]),
  ui: z
    .object({
      parsed: z
        .object({ kind: z.enum(["text", "table"]) })
        .passthrough()
        .nullable(),
      privacy: z.record(z.string(), z.string()),
      personal_choice: z.string().nullable(),
      changed_upstream: z.boolean(),
      readMs: z.number().optional(),
      readUntil: z.number().optional(),
      settlesTo: z
        .object({
          status: z.enum(STATUSES),
          status_note: z.string().nullable(),
        })
        .optional(),
    })
    .passthrough(),
});

export const SourceSummarySchema = z.object({
  draft: z.boolean(),
  snapshot_digest: z.string().nullable(),
  sources: z.array(SourceSchema),
  validation_status: z.string().nullable(),
});

const ProposalItemSchema = z
  .object({
    id: z.string().min(1),
    kind: z.string().min(1),
    title: z.string().min(1),
    detail: z.string().nullable().optional(),
    cite: z
      .object({
        sourceId: z.string().min(1),
        ref: z.string().min(1),
        excerpt: z.string().optional(),
      })
      .passthrough()
      .optional(),
    inferred: z.boolean().optional(),
    blocking: z.boolean().optional(),
    options: z.array(z.object({}).passthrough()).optional(),
  })
  .passthrough();

const PreparedDraftSchema = z.object({
  draft_digest: z.string().min(1),
  sources: z.array(
    z.object({
      pin: z.object({
        source_id: z.string().min(1),
        role: z.enum(["policy", "reference", "seed_records"]),
      }),
      statements: z.array(
        z.object({ statement_id: z.string().min(1) }).passthrough(),
      ),
    }),
  ),
  proposals: z.object({
    field_mappings: z.array(
      z.object({
        source_id: z.string().min(1),
        mapping: z.object({}).passthrough(),
      }),
    ),
    policy_rules: z.array(
      z
        .object({ statement_id: z.string().min(1), status: z.string() })
        .passthrough(),
    ),
    relationships: z.array(
      z.object({ relationship: z.object({}).passthrough() }),
    ),
  }),
  diagnostics: z.array(z.object({}).passthrough()),
});

export const SourcePrepareResponseSchema = z.object({
  import_ref: z.string().min(1),
  draft_digest: z.string().min(1),
  draft: PreparedDraftSchema,
  suggested_purposes: z.record(z.string(), z.string()),
  capabilities: SourceCapabilitiesSchema,
  ui: z.object({
    readMs: z.number().min(0),
    sources: z.array(SourceSchema),
    items: z.array(ProposalItemSchema),
  }),
});

export const SourceAcceptResponseSchema = z.object({
  draft: DraftRefSchema.nullable(),
  snapshot_digest: z.string().min(1),
  sources: z.array(SourceSchema),
  diagnostics: z.array(z.object({}).passthrough()),
  validation: z
    .object({
      status: z.string(),
      affected_scenarios: z.array(z.object({}).passthrough()),
    })
    .passthrough(),
  ui: z.object({
    accepted: z.array(ProposalItemSchema),
    failed: z.array(ProposalItemSchema),
  }),
});
