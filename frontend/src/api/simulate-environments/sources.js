import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addSampleEdits } from "./draftReview";
import {
  KNOWLEDGE_BASES,
  draftOfSources,
  proposalItemsOf,
  readSource,
  sampleCapabilities,
  sampleSourceSummary,
} from "./_fixtures/sources";
import {
  SourceAcceptResponseSchema,
  SourceCapabilitiesSchema,
  SourcePrepareResponseSchema,
  SourceSummarySchema,
} from "./sources.contract";

const CHECK_CONTRACT = import.meta.env.DEV || import.meta.env.MODE === "test";
const IMPORT_PREFIX = "stub-import:";
const STUB_DRAFT_ID = "9e3b1f4a-2c6d-4e8f-a0b1-c2d3e4f5a6b7";

// Sample data shaped like PR #4158's sources API, until it ships. Nothing
// here calls the backend: reads come from fixtures, writes go to the cache.
export const sourceSummaryKey = (envId) => ["harness-env-sources", envId];
export const sourceCapabilitiesKey = (envId) => [
  ...sourceSummaryKey(envId),
  "capabilities",
];

const checked = (schema, value) => {
  if (CHECK_CONTRACT) schema.parse(value);
  return value;
};

export const useEnvironmentSources = (envId) =>
  useQuery({
    queryKey: sourceSummaryKey(envId),
    queryFn: async () => checked(SourceSummarySchema, sampleSourceSummary()),
    enabled: Boolean(envId),
    // A refetch would drop every file added to the sample.
    staleTime: Infinity,
    meta: { errorHandled: true },
  });

export const useSourceCapabilities = (envId) =>
  useQuery({
    queryKey: sourceCapabilitiesKey(envId),
    queryFn: async () =>
      checked(SourceCapabilitiesSchema, sampleCapabilities()),
    enabled: Boolean(envId),
    staleTime: Infinity,
    meta: { errorHandled: true },
  });

const summaryOf = (queryClient, envId) =>
  queryClient.getQueryData(sourceSummaryKey(envId)) || sampleSourceSummary();

const settle = (source) =>
  source.status === "reading" ? { ...source, ...source.ui.settlesTo } : source;

// Reads every file, skips ones the environment already has (same name and
// size, or the same Knowledge base file), and returns #4158's prepare shape.
// Each new file starts `reading` and settles to `ui.settlesTo` at `ui.readUntil`.
const prepare = (queryClient, envId, files) => {
  const known = new Set(
    summaryOf(queryClient, envId).sources.map((source) => source.source_id),
  );
  const settled = files.map(readSource).filter((source) => {
    if (known.has(source.source_id)) return false;
    known.add(source.source_id);
    return true;
  });
  const now = Date.now();
  const sources = settled.map((source, index) => {
    const readMs = Math.min(1500 + index * 600, 3000);
    return {
      ...source,
      status: "reading",
      status_note: null,
      ui: {
        ...source.ui,
        readMs,
        readUntil: now + readMs,
        settlesTo: { status: source.status, status_note: source.status_note },
      },
    };
  });
  const ids = settled.map((source) => source.source_id);
  const digest = `sha256:stub-${ids.join("-") || "empty"}`;
  const draft = draftOfSources(settled, digest);
  return checked(SourcePrepareResponseSchema, {
    import_ref: `${IMPORT_PREFIX}${ids.join(",")}`,
    draft_digest: digest,
    draft,
    suggested_purposes: Object.fromEntries(
      draft.sources.map(({ pin }) => [pin.source_id, pin.role]),
    ),
    capabilities:
      queryClient.getQueryData(sourceCapabilitiesKey(envId)) ||
      sampleCapabilities(),
    ui: {
      readMs: Math.max(0, ...sources.map((source) => source.ui.readMs)),
      sources,
      items: proposalItemsOf(settled),
    },
  });
};

const usePrepare = (envId, filesOf) => {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: `env-sources-${envId}` },
    meta: { errorHandled: true },
    mutationFn: async (body) =>
      prepare(queryClient, envId, await filesOf(body)),
    onSuccess: (result) => {
      queryClient.setQueryData(sourceSummaryKey(envId), (summary) => {
        const current = summary || sampleSourceSummary();
        return {
          ...current,
          sources: [...current.sources, ...result.ui.sources],
        };
      });
      // Stands in for the backend reporting each file read.
      result.ui.sources.forEach((source) =>
        setTimeout(
          () =>
            queryClient.setQueryData(sourceSummaryKey(envId), (summary) =>
              summary
                ? {
                    ...summary,
                    sources: summary.sources.map((one) =>
                      one.source_id === source.source_id ? settle(one) : one,
                    ),
                  }
                : summary,
            ),
          source.ui.readMs,
        ),
      );
    },
  });
};

const textOf = async (file, ext) =>
  ["md", "txt"].includes(ext) && typeof file.text === "function"
    ? file.text()
    : undefined;

// mutate({ files: File[], purpose?, versionId, draft }). `purpose` is ignored:
// each file's role comes from reading it.
export const usePrepareUploadSource = (envId) =>
  usePrepare(envId, ({ files }) =>
    Promise.all(
      files.map(async (file) => ({
        name: file.name,
        size: file.size,
        text: await textOf(file, file.name.split(".").pop().toLowerCase()),
        origin: { kind: "upload" },
      })),
    ),
  );

// mutate({ selection: { kind: "knowledge_base", kb_id, file_ids | whole_collection } }).
// Files the person can't open are left out, as the backend refuses them.
export const usePrepareKbSource = (envId) =>
  usePrepare(envId, async ({ selection }) => {
    const kb = KNOWLEDGE_BASES.find((one) => one.kb_id === selection.kb_id);
    if (!kb) throw new Error("Knowledge base not found");
    return kb.files
      .filter(
        (file) =>
          file.access &&
          (selection.whole_collection ||
            selection.file_ids?.includes(file.file_id)),
      )
      .map((file) => ({
        name: file.name,
        size: file.size,
        origin: {
          kind: "knowledge_base",
          kb_id: kb.kb_id,
          file_id: file.file_id,
          collection: kb.name,
        },
      }));
  });

// Personal-data choices as a change summary words them.
const PERSONAL_CHOICES = {
  keep: "keep",
  replace: "replace with synthetic values",
  none: "no personal data present",
  custom: "custom, per column",
};

// Where a change came from: the file, and the cited spot when there is one.
const readFrom = (sourceId, name, cite) => {
  const prefix = `${name} · `;
  const place = cite?.ref?.startsWith(prefix)
    ? cite.ref.slice(prefix.length)
    : null;
  return {
    origin: "manual",
    agent: { kind: "user", user_id: "sample-user" },
    at: new Date().toISOString(),
    draft_revision: 1,
    used: [
      {
        kind: "source",
        source_id: sourceId,
        name,
        ...(place && { place }),
        ...(cite?.at && { locator: cite.at }),
      },
    ],
  };
};

// What each proposal changes, so Review changes files it under that area.
// Anything else (background for the agent) is the file itself.
const CHANGE_KINDS = {
  rule: "rule",
  conflict: "rule",
  table: "world_state",
  state: "world_state",
  tool: "tool",
  actor: "actor",
  scenario: "scenario",
};
// Scenarios name these, so they touch a list of scenarios, not all.
const NAMED = ["tool", "actor", "scenario"];

// Added from Review proposed changes, so it arrives accepted.
const fileChange = ({ kind, key, summary, from }) => ({
  id: `${kind}:${key}`,
  piece: kind === "scenario" ? "suite" : "environment",
  kind,
  key,
  document: "sources.json",
  action: "added",
  fields: [],
  patch: [{ op: "add", path: "", value: summary }],
  base_digest: null,
  draft_digest: null,
  summary,
  decision: "accepted",
  affects: NAMED.includes(kind) ? [] : "all",
  ...(kind === "world_state" && { rows: [] }),
  sources: [from],
  depends_on: [],
  graded: false,
  revalidate: [],
});

/**
 * Accepted proposals become accepted changes in Review changes, one per item;
 * blocked ones land in Couldn't apply. The same ids replace, so adding a
 * file's proposals again doesn't double them.
 */
export const appendAcceptedEdits = (
  queryClient,
  envId,
  { accepted = [], failed = [] },
) => {
  const names = new Map(
    summaryOf(queryClient, envId).sources.map((source) => [
      source.source_id,
      source.name,
    ]),
  );
  const nameOf = (id) => names.get(id) || id;
  addSampleEdits(queryClient, envId, {
    changes: accepted.map((item) => {
      const sourceId = item.cite?.sourceId || item.pick?.sourceId;
      const rule = ["rule", "conflict"].includes(item.kind)
        ? item.edited || item.pick?.rule || item.rule
        : null;
      return fileChange({
        kind: CHANGE_KINDS[item.kind] || "source",
        key: item.id,
        summary: rule ? `Rule: ${rule}` : item.title,
        from: readFrom(sourceId, nameOf(sourceId), item.cite),
      });
    }),
    failed: failed.map((item) => {
      const sourceId =
        item.sourceId ||
        item.cite?.sourceId ||
        item.options?.[0]?.sourceId ||
        null;
      return {
        id: `failed:${item.id}`,
        detail: item.title,
        reason: item.detail || "It couldn't be applied.",
        source_id: sourceId,
        scenarios: item.affects || [],
        ...(sourceId && {
          source_name: nameOf(sourceId),
          fix: item.kind === "missing" ? "supply" : "attach",
        }),
      };
    }),
  });
};

/**
 * A file's personal-data choice as one accepted change in Review changes,
 * however often it changes: a later choice replaces the row. Does nothing
 * until the file is added.
 */
export const upsertPersonalDataEdit = (queryClient, envId, source) => {
  const choice = source?.ui?.personal_choice;
  if (!choice || ["reading", "not_saved"].includes(source.status)) return;
  addSampleEdits(queryClient, envId, {
    changes: [
      fileChange({
        kind: "world_state",
        key: `personal-${source.source_id}`,
        summary: `${source.name}: personal data, ${PERSONAL_CHOICES[choice] || choice}`,
        from: readFrom(source.source_id, source.name),
      }),
    ],
  });
};

const idsOf = (importRef) =>
  new Set(importRef.slice(IMPORT_PREFIX.length).split(","));

const savedStatusOf = (source) => {
  const read = settle(source);
  if (read.status !== "not_saved") return read;
  return read.ui.parsed?.rejected?.length
    ? { ...read, status: "rows_rejected" }
    : { ...read, status: "ready", status_note: null };
};

// mutate({ import_ref, expected_environment_version_id, expected_draft,
// expected_draft_digest, review: buildSourceReview(...), ui?: { accepted, failed } }).
// `ui` carries the proposal items the person added and the blocked ones left out.
export const useAcceptSource = (envId) => {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: `env-sources-${envId}` },
    meta: { errorHandled: true },
    mutationFn: async ({
      import_ref: importRef,
      expected_draft: draft,
      ui,
    }) => {
      const ids = idsOf(importRef);
      const sources = summaryOf(queryClient, envId).sources.map((source) =>
        ids.has(source.source_id) ? savedStatusOf(source) : source,
      );
      return checked(SourceAcceptResponseSchema, {
        draft: { id: STUB_DRAFT_ID, revision: (draft?.revision ?? 0) + 1 },
        snapshot_digest: `sha256:stub-snapshot-${sources.length}`,
        sources,
        diagnostics: [],
        validation: { status: "structural_only", affected_scenarios: [] },
        ui: { accepted: ui?.accepted || [], failed: ui?.failed || [] },
      });
    },
    onSuccess: (result, { import_ref: importRef }) => {
      queryClient.setQueryData(sourceSummaryKey(envId), (summary) => ({
        ...(summary || sampleSourceSummary()),
        draft: true,
        snapshot_digest: result.snapshot_digest,
        sources: result.sources,
        validation_status: "accepted",
      }));
      queryClient.setQueryData(sourceCapabilitiesKey(envId), (capabilities) =>
        capabilities ? { ...capabilities, draft: result.draft } : capabilities,
      );
      appendAcceptedEdits(queryClient, envId, result.ui);
      const ids = idsOf(importRef);
      result.sources
        .filter((source) => ids.has(source.source_id))
        .forEach((source) =>
          upsertPersonalDataEdit(queryClient, envId, source),
        );
    },
  });
};
