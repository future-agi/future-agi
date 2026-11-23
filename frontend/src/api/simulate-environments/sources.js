import { useCallback } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { ModelHubKnowledgeBaseFilesCreateBody } from "src/generated/api-contracts/api.zod";
import {
  ROLES,
  VISIBILITY,
  isSaved,
} from "src/sections/simulate/environments/workspace/sources/sources.constants";
import axios, { endpoints } from "src/utils/axios";
import { addSampleEdits, draftReviewKey, sampleFileEdits } from "./draftReview";
import { ALL_SCENARIOS, sampleDraftReview } from "./_fixtures/draftReview";
import { harnessEnvironmentKey } from "./environment";
import {
  draftOfSources,
  kbRefreshed,
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

// Sample data shaped like the environment sources API, until it ships. Only
// the Knowledge base list and its files are real; reads come from fixtures
// and writes go to the cache.
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
// size, or the same Knowledge base file), and returns the sources API's
// prepare shape. Each new file starts `reading` and settles to `ui.settlesTo`
// at `ui.readUntil`; one the reader doesn't take by extension is refused at
// once, unread.
const prepare = (queryClient, envId, files, owner) => {
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
    if (source.status === "unsupported")
      return { ...source, ui: { ...source.ui, owner, readMs: 0 } };
    const readMs = Math.min(1500 + index * 600, 3000);
    return {
      ...source,
      status: "reading",
      status_note: null,
      ui: {
        ...source.ui,
        owner,
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
  const { user } = useSafeAuthContext();
  // Who added the file, by first name, as the backend will send it.
  const owner = user?.name?.split(" ")[0] || "You";
  return useMutation({
    scope: { id: `env-sources-${envId}` },
    meta: { errorHandled: true },
    mutationFn: async (body) =>
      prepare(queryClient, envId, await filesOf(body), owner),
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

// The person's Knowledge bases and their files, from the Knowledge base API,
// a page at a time. Both endpoints count `total_rows` after search but before
// status, so rows the picker hides still use up a page.
const KB_PAGE_SIZE = 20;
// "Add all" reads every page of the Knowledge base, so it takes bigger ones.
const KB_COPY_PAGE_SIZE = 100;
const kbListKey = ["knowledge-base", "env-sources-picker"];

// Failed ones have no file read; Deleting ones are on their way out.
const KB_HIDDEN = new Set(["Failed", "Deleting"]);
// PartialCompleted: some files failed, the rest are ready.
export const isKbReady = (kb) =>
  kb.status === "Completed" || kb.status === "PartialCompleted";
export const isKbFileReady = (file) => file.status === "Completed";

// Neither endpoint orders its rows, so one can repeat across pages.
const pagedQuery = (queryKey, fetchPage) => ({
  queryKey,
  queryFn: ({ pageParam }) => fetchPage(pageParam),
  initialPageParam: 0,
  getNextPageParam: (last, pages, lastPage) =>
    last.raw > 0 && (lastPage + 1) * KB_PAGE_SIZE < last.total
      ? lastPage + 1
      : undefined,
  select: ({ pages }) => {
    const seen = new Set();
    return pages
      .flatMap((page) => page.rows)
      .filter((row) => !seen.has(row.id) && seen.add(row.id));
  },
  // "always": a Knowledge base made in another tab shows on coming back,
  // even within the minute the list stays fresh.
  refetchOnWindowFocus: "always",
  staleTime: 60 * 1000,
  meta: { errorHandled: true },
});

const knowledgeBasesQuery = (search) =>
  pagedQuery([...kbListKey, "list", search], async (pageNumber) => {
    const { data } = await axios.get(endpoints.knowledge.list, {
      params: {
        ...(search && { search }),
        page_number: pageNumber,
        page_size: KB_PAGE_SIZE,
      },
    });
    // An organisation with no Knowledge bases gets no `table_data`.
    const rows = data?.result?.table_data || [];
    return {
      raw: rows.length,
      total: data?.result?.total_rows ?? 0,
      rows: rows
        .filter((kb) => !KB_HIDDEN.has(kb.status))
        .map((kb) => ({
          id: kb.id,
          name: kb.name,
          status: kb.status,
          file_count: kb.files_uploaded,
          updated_at: kb.updated_at,
        })),
    };
  });

const fetchKbFiles = async (kbId, search, pageNumber, pageSize) => {
  const body = checked(ModelHubKnowledgeBaseFilesCreateBody, {
    kb_id: kbId,
    ...(search && { search }),
    page_number: pageNumber,
    page_size: pageSize,
  });
  const { data } = await axios.post(endpoints.knowledge.files, body);
  const rows = data?.result?.table_data || [];
  return {
    raw: rows.length,
    total: data?.result?.total_rows ?? 0,
    rows: rows
      .filter((file) => file.status !== "Deleting")
      .map((file) => ({
        id: file.id,
        name: file.name,
        size: file.file_size,
        status: file.status,
      })),
  };
};

// Every ready file in the Knowledge base, whatever the picker has loaded.
const readyKbFiles = async (kbId) => {
  const ready = [];
  for (let page = 0; ; page += 1) {
    const { raw, total, rows } = await fetchKbFiles(
      kbId,
      "",
      page,
      KB_COPY_PAGE_SIZE,
    );
    ready.push(...rows.filter(isKbFileReady));
    if (!raw || (page + 1) * KB_COPY_PAGE_SIZE >= total) return ready;
  }
};

// `data` is the rows loaded so far; fetchNextPage loads the next page.
export const useKnowledgeBases = ({ enabled = true, search = "" } = {}) =>
  useInfiniteQuery({ ...knowledgeBasesQuery(search), enabled });

export const useKnowledgeBaseFiles = (kbId, { search = "" } = {}) =>
  useInfiniteQuery({
    ...pagedQuery([...kbListKey, "files", kbId, search], (pageNumber) =>
      fetchKbFiles(kbId, search, pageNumber, KB_PAGE_SIZE),
    ),
    enabled: Boolean(kbId),
  });

// mutate({ selection: { kind: "knowledge_base", kb, files | whole_collection } }),
// `kb` and `files` as the picker lists them. "Add all" copies the ready files.
// The Knowledge base and its files are real; copying them in is the stub.
export const usePrepareKbSource = (envId) =>
  usePrepare(envId, async ({ selection: { kb, files, whole_collection } }) =>
    (whole_collection ? await readyKbFiles(kb.id) : files).map((file) => ({
      name: file.name,
      size: file.size,
      origin: {
        kind: "knowledge_base",
        kb_id: kb.id,
        file_id: file.id,
        collection: kb.name,
      },
    })),
  );

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
// The key of the one change holding every scenario proposed from files.
const FILE_SCENARIOS = "files";

// The sample scenarios a change makes graded runs check again: rules and
// records touch every one, an actor or a tool a few. Background is ungraded.
const RECHECKED = {
  rule: ALL_SCENARIOS,
  world_state: ALL_SCENARIOS,
  actor: ALL_SCENARIOS.slice(0, 3),
  tool: ALL_SCENARIOS.slice(-6),
  source: [],
};

// Added from Review proposed changes, so it arrives accepted. `patch` is
// what changes, read as the diff.
const fileChange = ({
  kind,
  key,
  summary,
  patch,
  from,
  revalidate = RECHECKED[kind],
}) => ({
  id: `${kind}:${key}`,
  piece: kind === "scenario" ? "suite" : "environment",
  kind,
  key,
  document: "sources.json",
  action: "added",
  fields: [],
  patch,
  base_digest: null,
  draft_digest: null,
  summary,
  decision: "accepted",
  affects: NAMED.includes(kind) ? revalidate : "all",
  ...(kind === "world_state" && { rows: [] }),
  sources: [from],
  depends_on: [],
  graded: revalidate.length > 0,
  revalidate,
});

const added = (value, path = "") => ({ op: "add", path, value });

const ruleNumber = (change) =>
  Number(/\bR(\d+)\b/.exec(change.summary)?.[1]) || 0;

/**
 * Accepted proposals become accepted changes in Review changes, one per item
 * (scenarios share one); blocked ones land in Couldn't apply. The same ids
 * replace, so adding a file's proposals again doesn't double them.
 */
export const appendAcceptedEdits = (
  queryClient,
  envId,
  { accepted = [], failed = [] },
) => {
  const sources = new Map(
    summaryOf(queryClient, envId).sources.map((source) => [
      source.source_id,
      source,
    ]),
  );
  const nameOf = (id) => sources.get(id)?.name || id;
  const fromItem = (item) => {
    const sourceId = item.cite?.sourceId || item.pick?.sourceId;
    return readFrom(sourceId, nameOf(sourceId), item.cite);
  };
  const review = queryClient.getQueryData(draftReviewKey(envId));
  const prior = sampleFileEdits(review).changes;
  // New rules number on from the highest in the environment and the draft
  // (the sample joins a review not loaded yet); one added again keeps its number.
  const numbers = new Map(
    (review || sampleDraftReview()).changes
      .filter((change) => change.kind === "rule")
      .map((change) => [change.id, ruleNumber(change)]),
  );
  let highest = Math.max(
    queryClient.getQueryData(harnessEnvironmentKey(envId))?.contract
      ?.hard_constraints?.length || 0,
    ...numbers.values(),
  );
  const numberOf = (id) => {
    if (!numbers.has(id)) {
      highest += 1;
      numbers.set(id, highest);
    }
    return numbers.get(id);
  };
  // The first table added moves the world off generated data, once per
  // environment: the change that carries it keeps it when added again.
  let movesWorld = prior.find((change) =>
    change.patch.some((op) => op.path === "/world_data"),
  )?.id;
  const actorRows = new Map(
    prior
      .filter((change) => change.kind === "actor")
      .map((change) => [change.id, change]),
  );
  const changeOf = (item) => {
    const kind = CHANGE_KINDS[item.kind] || "source";
    const base = { kind, key: item.id, from: fromItem(item) };
    if (kind === "rule") {
      const text = item.edited || item.pick?.rule || item.rule;
      const n = numberOf(`rule:${item.id}`);
      return fileChange({
        ...base,
        summary: `Rule R${n} added: ${text}`,
        patch: [added(`R${n}: ${text}`)],
      });
    }
    if (item.kind === "table") {
      const source = sources.get(item.cite?.sourceId);
      const parsed = source?.ui?.parsed || {};
      movesWorld = movesWorld || `${kind}:${item.id}`;
      return fileChange({
        ...base,
        summary: `New table ${parsed.table} · ${(parsed.accepted ?? 0).toLocaleString()} rows from ${source?.name}`,
        patch: [
          added(parsed.table, "/tables"),
          ...(movesWorld === `${kind}:${item.id}`
            ? [
                { op: "test", path: "/world_data", value: "generated" },
                {
                  op: "replace",
                  path: "/world_data",
                  value: "your records (a private sample)",
                },
              ]
            : []),
        ],
      });
    }
    if (item.kind === "actor") {
      // One row per actor however many files propose it: the first proposer's
      // citation stays, the latest edited approval scope wins.
      const key = item.actor?.name || item.id;
      const first = actorRows.get(`${kind}:${key}`);
      const row =
        first && !item.edited
          ? first
          : {
              ...fileChange({
                ...base,
                key,
                summary: item.edited
                  ? `${item.title} · approval scope: ${item.edited}`
                  : item.title,
                patch: [added(item.actor || item.title)],
              }),
              ...(first && { sources: first.sources }),
            };
      actorRows.set(row.id, row);
      return row;
    }
    let value = item.title;
    if (item.kind === "state")
      value = item.title.replace(/^Starting state: /, "");
    if (kind === "source") value = nameOf(item.cite?.sourceId);
    return fileChange({ ...base, summary: item.title, patch: [added(value)] });
  };
  // A failed row clears once what it waited on is added: the table it
  // needed, or the same item added after all.
  const supplied = new Set(
    accepted
      .filter((item) => item.kind === "table")
      .map((item) => sources.get(item.cite?.sourceId)?.ui?.parsed?.table)
      .filter(Boolean),
  );
  const addedIds = new Set(accepted.map((item) => `failed:${item.id}`));
  const cleared = (review?.failed || [])
    .filter((row) => supplied.has(row.ui?.needs) || addedIds.has(row.id))
    .map((row) => row.id);
  // Every scenario proposed from files is one change, as the design has it:
  // the union by name across batches, each cited by the item that first
  // proposed it (`sources` lines up with `patch`).
  const scenarioItems = accepted.filter((item) => item.kind === "scenario");
  const priorScenarios = prior.find(
    (change) => change.id === `scenario:${FILE_SCENARIOS}`,
  );
  const proposed = new Map(
    (priorScenarios?.patch || []).map((op, i) => [
      op.value,
      priorScenarios.sources[i],
    ]),
  );
  scenarioItems.forEach((item) =>
    (item.scenario || []).forEach((one) => {
      if (!proposed.has(one.t)) proposed.set(one.t, fromItem(item));
    }),
  );
  const names = [...proposed.keys()];
  addSampleEdits(queryClient, envId, {
    changes: [
      // Two files in one batch proposing one actor still make one row.
      ...new Map(
        accepted
          .filter((item) => item.kind !== "scenario")
          .map(changeOf)
          .map((change) => [change.id, change]),
      ).values(),
      ...(scenarioItems.length
        ? [
            {
              ...fileChange({
                kind: "scenario",
                key: FILE_SCENARIOS,
                summary: `${names.length} proposed scenario${names.length === 1 ? "" : "s"} added`,
                patch: names.map((name) => added(name)),
                revalidate: names,
              }),
              sources: [...proposed.values()],
            },
          ]
        : []),
    ],
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
        ...(item.needs && { ui: { needs: item.needs } }),
      };
    }),
    cleared,
  });
};

const personalWords = (choice) =>
  PERSONAL_CHOICES[choice] || choice || "not chosen";

const lowerFirst = (text) => text.charAt(0).toLowerCase() + text.slice(1);

// What a mapping edit changed against the saved copy, one phrase each.
const mappingLines = (saved, now) => {
  const was = new Map(
    (saved.ui.parsed.columns || []).map((column) => [column.name, column]),
  );
  const placed = (column) => {
    if (column.leftOut) return "left out";
    return column.mappedTo ? `mapped to ${column.mappedTo}` : "not placed";
  };
  const lines = (now.ui.parsed.columns || []).flatMap((column) => {
    const before = was.get(column.name) || {};
    const { name } = column;
    return [
      column.type !== before.type && `${name} is ${column.type}`,
      Boolean(column.stableId) !== Boolean(before.stableId) &&
        `${name} ${column.stableId ? "is" : "isn't"} a stable ID`,
      (column.ref || null) !== (before.ref || null) &&
        `${name} points at ${column.ref || "nothing"}`,
      before.proposal &&
        !column.proposal &&
        `${name} isn't ${before.proposal.to}`,
      placed(column) !== placed(before) && `${name} ${placed(column)}`,
    ].filter(Boolean);
  });
  const joins = { ...saved.ui.joins, ...now.ui.joins };
  Object.keys(joins).forEach((field) => {
    const omitted = now.ui.joins?.[field] === "omit";
    if (omitted !== (saved.ui.joins?.[field] === "omit"))
      lines.push(`${field}: rows ${omitted ? "left out" : "put back"}`);
  });
  return lines;
};

const labelOf = (list, id) => list.find((one) => one.id === id)?.label || id;

/**
 * The parts of a file in the environment each file drawer edit changes. Each
 * kind is one Review changes row: `part` is what the accepted copy keeps (and
 * a revert writes back), `lines` says what moved since and `summary` starts
 * the row's title after the file name.
 */
const FILE_EDITS = {
  mapping: {
    path: "/mapping",
    part: (source) => ({
      status: source.status,
      status_note: source.status_note,
      ui: {
        joins: source.ui?.joins,
        id_mapping: source.ui?.id_mapping,
        parsed: {
          columns: source.ui?.parsed?.columns,
          rejected: source.ui?.parsed?.rejected,
          accepted: source.ui?.parsed?.accepted,
        },
      },
    }),
    lines: mappingLines,
  },
  usage: {
    path: "/used_as",
    part: ({ role, purpose, visibility }) => ({ role, purpose, visibility }),
    lines: (saved, now) =>
      [
        now.role !== saved.role && `used as ${labelOf(ROLES, now.role)}`,
        now.visibility !== saved.visibility &&
          `who reads it: ${lowerFirst(labelOf(VISIBILITY, now.visibility) || "nobody")}`,
      ].filter(Boolean),
  },
  personal: {
    path: "/personal_data",
    summary: "personal data, ",
    part: (source) => ({
      ui: {
        personal_choice: source.ui?.personal_choice,
        privacy: source.ui?.privacy,
      },
    }),
    lines: (saved, now) =>
      JSON.stringify(saved) === JSON.stringify(now)
        ? []
        : [personalWords(now.ui.personal_choice)],
  },
};

const savedCopyOf = (source) =>
  Object.fromEntries(
    Object.entries(FILE_EDITS).map(([kind, edit]) => [kind, edit.part(source)]),
  );

const withPart = (source, { ui = {}, ...top }) => ({
  ...source,
  ...top,
  ui: {
    ...source.ui,
    ...ui,
    ...(ui.parsed && { parsed: { ...source.ui.parsed, ...ui.parsed } }),
  },
});

// Already accepted; `ui` names the file and kind a revert writes back.
const fileEditRow = (kind, source, lines) => ({
  ...fileChange({
    kind: "world_state",
    key: `${kind}-${source.source_id}`,
    summary: `${source.name}: ${FILE_EDITS[kind].summary || ""}${lines.join(", ")}`,
    patch: lines.map((line) => added(line, FILE_EDITS[kind].path)),
    from: readFrom(source.source_id, source.name),
  }),
  ui: { source_id: source.source_id, edit: kind },
});

/**
 * A file's personal-data choice as one accepted change in Review changes,
 * however often it changes: a later choice replaces the row. Does nothing
 * until the file is added.
 */
export const upsertPersonalDataEdit = (queryClient, envId, source) => {
  const choice = source?.ui?.personal_choice;
  if (!choice || ["reading", "not_saved"].includes(source.status)) return;
  addSampleEdits(queryClient, envId, {
    changes: [fileEditRow("personal", source, [personalWords(choice)])],
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
// expected_draft_digest, review: buildSourceReview(...), ui?: { accepted, failed, privacy } }).
// `ui` carries the proposal items the person added, the blocked ones left out,
// and each custom file's column map (the API takes one mode per file).
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
      // The accepted scenarios that cite each file, for its Used by.
      const scenariosOf = (sourceId) =>
        (ui?.accepted || [])
          .filter(
            (item) =>
              item.kind === "scenario" && item.cite?.sourceId === sourceId,
          )
          .flatMap((item) => (item.scenario || []).map((one) => one.t));
      const sources = summaryOf(queryClient, envId).sources.map((source) => {
        if (!ids.has(source.source_id)) return source;
        const saved = savedStatusOf(source);
        return {
          ...saved,
          ui: {
            ...saved.ui,
            scenarios: scenariosOf(source.source_id),
            // What later drawer edits compare with and revert to.
            saved: savedCopyOf(saved),
          },
        };
      });
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
      queryClient.setQueryData(sourceSummaryKey(envId), (summary) => {
        const current = summary || sampleSourceSummary();
        return {
          ...current,
          draft: true,
          snapshot_digest: result.snapshot_digest,
          sources: result.sources,
          validation_status: "accepted",
          ui: { ...current.ui, saved_at: new Date().toISOString() },
        };
      });
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

// Edits to the environment's files. Each writes the summary cache until the
// sources API takes them; swap the bodies, not the callers.
const updateSources = (queryClient, envId, change) =>
  queryClient.setQueryData(sourceSummaryKey(envId), (current) =>
    current ? { ...current, sources: change(current.sources) } : current,
  );

const editSource = (queryClient, envId, sourceId, change) => {
  let before;
  let after;
  updateSources(queryClient, envId, (sources) =>
    sources.map((one) => {
      if (one.source_id !== sourceId) return one;
      before = one;
      after = change(one);
      return after;
    }),
  );
  // A file not saved yet goes in with the builder's proposal instead.
  const saved = after?.ui?.saved;
  if (!saved || !isSaved(after)) return;
  // On a saved file each kind of edit is one draft row, gone once it matches
  // the saved copy again.
  const changes = [];
  const cleared = [];
  Object.entries(FILE_EDITS).forEach(([kind, edit]) => {
    const now = edit.part(after);
    if (JSON.stringify(edit.part(before)) === JSON.stringify(now)) return;
    const lines = edit.lines(saved[kind], now);
    if (lines.length) changes.push(fileEditRow(kind, after, lines));
    else cleared.push(`world_state:${kind}-${sourceId}`);
  });
  if (changes.length || cleared.length)
    addSampleEdits(queryClient, envId, { changes, cleared });
};

// (changes): the file drawer edits among them go back to the saved copy and
// leave Review changes (Reject, Undo, Discard all). Says whether any did.
export const useRevertFileEdits = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (changes) => {
      const edits = changes.filter((one) => FILE_EDITS[one.ui?.edit]);
      if (!edits.length) return false;
      updateSources(queryClient, envId, (sources) =>
        sources.map((source) =>
          edits
            .filter((one) => one.ui.source_id === source.source_id)
            .reduce(
              (one, edit) =>
                one.ui?.saved ? withPart(one, one.ui.saved[edit.ui.edit]) : one,
              source,
            ),
        ),
      );
      addSampleEdits(queryClient, envId, {
        cleared: edits.map((one) => one.id),
      });
      return true;
    },
    [queryClient, envId],
  );
};

// The open Review proposed changes, kept per environment outside the tab so
// leaving World & tools doesn't drop it. UI state, never sent.
const pendingReviewKey = (envId) => ["harness-env-sources-review", envId];

// (sourceId, change): `change` maps the file to its edited copy.
export const useUpdateSource = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (sourceId, change) => editSource(queryClient, envId, sourceId, change),
    [queryClient, envId],
  );
};

// (source): a saved file goes to Removed; one not saved yet is discarded.
export const useRemoveSource = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (source) => {
      if (["ready", "rows_rejected"].includes(source.status))
        editSource(queryClient, envId, source.source_id, (one) => ({
          ...one,
          ui: { ...one.ui, removed: true },
        }));
      else
        updateSources(queryClient, envId, (sources) =>
          sources.filter((one) => one.source_id !== source.source_id),
        );
      // A review whose files are all gone has nothing left to add, so it ends.
      const left = new Set(
        summaryOf(queryClient, envId).sources.map((one) => one.source_id),
      );
      queryClient.setQueryData(pendingReviewKey(envId), (review) =>
        review?.ui.sources.some((one) => left.has(one.source_id))
          ? review
          : null,
      );
    },
    [queryClient, envId],
  );
};

// (sourceId): a removed file comes back.
export const useRestoreSource = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (sourceId) =>
      editSource(queryClient, envId, sourceId, (one) => ({
        ...one,
        ui: { ...one.ui, removed: false },
      })),
    [queryClient, envId],
  );
};

// (sourceId, accepted): the Knowledge base copy takes the accepted changes.
export const useRefreshSource = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (sourceId, accepted) =>
      editSource(queryClient, envId, sourceId, (one) =>
        kbRefreshed(one, accepted),
      ),
    [queryClient, envId],
  );
};

// (sourceId, { choice, privacy }): the file's personal-data choice and column map.
export const useSetPersonalData = (envId) => {
  const queryClient = useQueryClient();
  return useCallback(
    (sourceId, { choice, privacy }) =>
      editSource(queryClient, envId, sourceId, (one) => ({
        ...one,
        ui: { ...one.ui, personal_choice: choice, privacy },
      })),
    [queryClient, envId],
  );
};

export const usePendingSourceReview = (envId) => {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: pendingReviewKey(envId),
    queryFn: () => queryClient.getQueryData(pendingReviewKey(envId)) ?? null,
    enabled: Boolean(envId),
    staleTime: Infinity,
    // Nothing watches it while the tab is away.
    gcTime: Infinity,
  });
  const setReview = useCallback(
    (update) => queryClient.setQueryData(pendingReviewKey(envId), update),
    [queryClient, envId],
  );
  return [data ?? null, setReview];
};
