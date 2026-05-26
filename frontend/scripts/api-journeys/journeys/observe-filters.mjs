/* global process */
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  ApiJourneyError,
  apiPath,
  asArray,
  assert,
  currentUserEmail,
  currentUserId,
  isUuid,
  requireMutations,
  skip,
} from "../lib/api-client.mjs";
import {
  assertNoCamelGenAi,
  canonicalNumberFilter,
  canonicalTextFilter,
  queuePath,
  queryWithFilters,
  resolveObserveProject,
  resolveQueue,
} from "../lib/fixtures.mjs";

const EMPTY_FILTERS = [];
const LATENCY_GTE_ZERO = canonicalNumberFilter(
  "latency",
  "greater_than_or_equal",
  0,
);
const execFileAsync = promisify(execFile);

export const observeFilterJourneys = [
  {
    id: "OBS-API-001",
    title:
      "Observe property inventory keeps raw attribute names and system metrics",
    tags: ["observe", "filters", "safe", "smoke"],
    async run({ client, evidence }) {
      const project = await resolveObserveProject(client, evidence);

      const systemMetrics = await client.get(
        apiPath("/tracer/project/fetch_system_metrics/"),
      );
      assert(
        asArray(systemMetrics).includes("latency"),
        "System metric inventory must include latency.",
      );

      const graphProperties = await client.get(
        apiPath("/tracer/trace/get_properties/"),
      );
      assert(
        asArray(graphProperties).includes("Average"),
        "Trace graph properties must include Average.",
      );

      const rowTypes = ["spans", "traces", "sessions", "voiceCalls"];
      const fieldCounts = {};
      for (const rowType of rowTypes) {
        const fields = await client.get(
          apiPath("/tracer/observation-span/get_observation_span_fields/"),
          {
            query: {
              filters: { project_id: project.id },
              row_type: rowType,
            },
          },
        );
        const fieldList = asArray(fields);
        assertNoCamelGenAi(fieldList);
        fieldCounts[rowType] = fieldList.length;
      }

      evidence.push({
        project_id: project.id,
        system_metrics: asArray(systemMetrics),
        field_counts: fieldCounts,
      });
    },
  },
  {
    id: "OBS-API-002",
    title:
      "Observe trace, span, session, user, voice list filters accept latency filter",
    tags: ["observe", "filters", "safe", "latency"],
    async run({ client, evidence }) {
      const project = await resolveObserveProject(client, evidence);

      const checks = [
        {
          name: "traces",
          path: apiPath("/tracer/trace/list_traces_of_session/"),
          query: { project_id: project.id, page_number: 0, page_size: 5 },
        },
        {
          name: "spans",
          path: apiPath("/tracer/observation-span/list_spans_observe/"),
          query: { project_id: project.id, page_number: 0, page_size: 5 },
        },
        {
          name: "sessions",
          path: apiPath("/tracer/trace-session/list_sessions/"),
          query: { project_id: project.id, page_number: 0, page_size: 5 },
        },
        {
          name: "users",
          path: apiPath("/tracer/users/"),
          query: {
            project_id: project.id,
            current_page_index: 0,
            page_size: 5,
          },
        },
        {
          name: "voice",
          path: apiPath("/tracer/trace/list_voice_calls/"),
          query: { project_id: project.id, page: 1, page_size: 5 },
        },
      ];

      const counts = {};
      for (const check of checks) {
        const base = await client.get(
          queryWithFilters(check.path, EMPTY_FILTERS, check.query),
        );
        const filtered = await client.get(
          queryWithFilters(check.path, LATENCY_GTE_ZERO, check.query),
        );
        const baseCount = responseCount(base);
        const filteredCount = responseCount(filtered);
        assert(
          filteredCount <= baseCount || baseCount === 0,
          `${check.name} latency filter returned more rows than the unfiltered query.`,
        );
        counts[check.name] = { base: baseCount, filtered: filteredCount };
      }

      evidence.push({ project_id: project.id, counts });
    },
  },
  {
    id: "OBS-API-003",
    title: "Observe graph APIs apply the same filters as list APIs",
    tags: ["observe", "filters", "safe", "graph"],
    async run({ client, evidence }) {
      const project = await resolveObserveProject(client, evidence);
      const graphBody = {
        project_id: project.id,
        filters: LATENCY_GTE_ZERO,
        interval: "day",
        property: "average",
        req_data_config: { type: "SYSTEM_METRIC", id: "latency" },
      };

      const traces = await client.post(
        apiPath("/tracer/trace/get_graph_methods/"),
        graphBody,
      );
      const spans = await client.post(
        apiPath("/tracer/observation-span/get_graph_methods/"),
        graphBody,
      );
      const sessions = await client.post(
        apiPath("/tracer/trace-session/get_session_graph_data/"),
        graphBody,
      );

      for (const [name, payload] of Object.entries({
        traces,
        spans,
        sessions,
      })) {
        assert(
          Array.isArray(payload?.data),
          `${name} graph response must include data array.`,
        );
      }

      evidence.push({
        project_id: project.id,
        trace_points: traces.data.length,
        span_points: spans.data.length,
        session_points: sessions.data.length,
      });
    },
  },
  {
    id: "OBS-API-004",
    title:
      "Observe saved view create, update, duplicate, reorder, and delete lifecycle",
    tags: ["observe", "saved-views", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const project = await resolveObserveProject(client, evidence);
      const viewName = `api journey view ${runId}`;
      const renamedView = `api journey view updated ${runId}`;

      const created = await client.post(apiPath("/tracer/saved-views/"), {
        project_id: project.id,
        name: viewName,
        tab_type: "traces",
        visibility: "personal",
        icon: "filter",
        config: {
          filters: LATENCY_GTE_ZERO,
          display: { viewMode: "list" },
        },
      });
      assert(created?.id, "Saved view create did not return id.");
      cleanup.defer("delete saved view", () =>
        client.delete(
          apiPath("/tracer/saved-views/{id}/", { id: created.id }),
          {
            query: { project_id: project.id },
            okStatuses: [200, 404],
          },
        ),
      );

      const detail = await client.get(
        apiPath("/tracer/saved-views/{id}/", { id: created.id }),
        { query: { project_id: project.id } },
      );
      assert(
        detail?.name === viewName,
        "Saved view detail returned wrong name.",
      );
      assert(
        detail?.config?.filters?.[0]?.filter_config?.filter_op ===
          "greater_than_or_equal",
        "Saved view detail did not preserve canonical filter config.",
      );

      const updated = await client.put(
        apiPath("/tracer/saved-views/{id}/", { id: created.id }),
        {
          name: renamedView,
          visibility: "personal",
          icon: "activity",
          config: {
            filters: LATENCY_GTE_ZERO,
            display: { viewMode: "graph" },
          },
        },
        { query: { project_id: project.id } },
      );
      assert(
        updated?.name === renamedView,
        "Saved view update did not persist name.",
      );
      assert(
        updated?.config?.display?.viewMode === "graph",
        "Saved view update did not persist display config.",
      );

      const duplicated = await client.post(
        apiPath("/tracer/saved-views/{id}/duplicate/", { id: created.id }),
        { name: `${renamedView} copy` },
        { query: { project_id: project.id } },
      );
      assert(duplicated?.id, "Saved view duplicate did not return id.");
      cleanup.defer("delete duplicated saved view", () =>
        client.delete(
          apiPath("/tracer/saved-views/{id}/", { id: duplicated.id }),
          {
            query: { project_id: project.id },
            okStatuses: [200, 404],
          },
        ),
      );

      await client.post(apiPath("/tracer/saved-views/reorder/"), {
        project_id: project.id,
        tab_type: "traces",
        order: [
          { id: created.id, position: 0 },
          { id: duplicated.id, position: 1 },
        ],
      });

      const listed = await client.get(apiPath("/tracer/saved-views/"), {
        query: { project_id: project.id },
      });
      const customViews = asArray(listed.custom_views);
      const listedCreated = customViews.find((view) => view.id === created.id);
      const listedDuplicate = customViews.find(
        (view) => view.id === duplicated.id,
      );
      assert(listedCreated, "Saved view list did not include created view.");
      assert(
        listedDuplicate,
        "Saved view list did not include duplicated view.",
      );
      assert(
        listedCreated.position <= listedDuplicate.position,
        "Saved view reorder did not preserve requested ordering.",
      );

      evidence.push({
        project_id: project.id,
        saved_view_id: created.id,
        duplicated_view_id: duplicated.id,
      });
    },
  },
  {
    id: "OBS-API-005",
    title: "Observe project and trace tags update and revert cleanly",
    tags: ["observe", "tags", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const project = await resolveObserveProject(client, evidence);
      const tag = `api-journey-${runId}`;
      const originalProjectTags = normalizeTags(project.tags);

      cleanup.defer("restore observe project tags", () =>
        client.patch(
          apiPath("/tracer/project/{id}/tags/", { id: project.id }),
          {
            tags: originalProjectTags,
          },
        ),
      );
      const projectTags = uniqueTags([...originalProjectTags, tag]);
      const updatedProject = await client.patch(
        apiPath("/tracer/project/{id}/tags/", { id: project.id }),
        { tags: projectTags },
      );
      assert(
        normalizeTags(updatedProject.tags).includes(tag),
        "Project tag update did not return the new tag.",
      );

      const traceList = await client.get(
        queryWithFilters(apiPath("/tracer/trace/list_traces_of_session/"), [], {
          project_id: project.id,
          page_number: 0,
          page_size: 10,
        }),
      );
      const trace = asArray(traceList).find((row) => row?.trace_id);
      if (!trace?.trace_id) {
        skip("No trace row is available for trace tag mutation coverage.");
      }
      const originalTraceTags = normalizeTags(trace.tags);
      cleanup.defer("restore observe trace tags", () =>
        client.patch(
          apiPath("/tracer/trace/{id}/tags/", { id: trace.trace_id }),
          {
            tags: originalTraceTags,
          },
        ),
      );

      const traceTags = uniqueTags([...originalTraceTags, tag]);
      const updatedTrace = await client.patch(
        apiPath("/tracer/trace/{id}/tags/", { id: trace.trace_id }),
        { tags: traceTags },
      );
      assert(
        normalizeTags(updatedTrace.tags).includes(tag),
        "Trace tag update did not return the new tag.",
      );

      const restoredTrace = await client.patch(
        apiPath("/tracer/trace/{id}/tags/", { id: trace.trace_id }),
        { tags: originalTraceTags },
      );
      assert(
        arraysEqual(normalizeTags(restoredTrace.tags), originalTraceTags),
        "Trace tag revert did not restore the original tags.",
      );

      const restoredProject = await client.patch(
        apiPath("/tracer/project/{id}/tags/", { id: project.id }),
        { tags: originalProjectTags },
      );
      assert(
        arraysEqual(normalizeTags(restoredProject.tags), originalProjectTags),
        "Project tag revert did not restore the original tags.",
      );

      evidence.push({
        project_id: project.id,
        trace_id: trace.trace_id,
        temporary_tag: tag,
      });
    },
  },
  {
    id: "OBS-API-006",
    title:
      "Observe session list, values, graph, and annotation queue add round-trip",
    tags: ["observe", "sessions", "annotation", "mutating", "data-roundtrip"],
    async run({ client, cleanup, evidence }) {
      requireMutations();
      const project = await resolveObserveProject(client, evidence);
      const queue = await resolveQueue(client, evidence);

      const baseList = await client.get(
        queryWithFilters(apiPath("/tracer/trace-session/list_sessions/"), [], {
          project_id: project.id,
          page_number: 0,
          page_size: 25,
        }),
      );
      const baseRows = asArray(baseList);
      if (!baseRows.length) {
        skip(
          "No observe session rows are available for session queue coverage.",
        );
      }

      const session = await findSessionNotAlreadyQueued(
        client,
        queue.id,
        baseRows,
      );
      if (!session?.session_id) {
        skip(
          "Every sampled observe session is already present in the selected queue.",
        );
      }

      const sessionFilter = canonicalTextFilter(
        "session_id",
        "equals",
        session.session_id,
      );
      const filteredList = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace-session/list_sessions/"),
          sessionFilter,
          {
            project_id: project.id,
            page_number: 0,
            page_size: 5,
          },
        ),
      );
      const filteredRows = asArray(filteredList);
      assert(
        filteredRows.some((row) => row.session_id === session.session_id),
        "Session list filter did not return the selected session.",
      );
      assert(
        Number(filteredList?.metadata?.total_rows ?? filteredRows.length) <=
          Number(baseList?.metadata?.total_rows ?? baseRows.length),
        "Filtered session list returned more rows than the base session list.",
      );

      const filterValues = await client.get(
        apiPath("/tracer/trace-session/get_session_filter_values/"),
        {
          query: {
            project_id: project.id,
            column: "session_id",
            search: session.session_id.slice(0, 8),
            page: 0,
            page_size: 20,
          },
        },
      );
      assert(
        asArray(filterValues.values).includes(session.session_id),
        "Session filter value lookup did not include the selected session id.",
      );

      const graph = await client.post(
        apiPath("/tracer/trace-session/get_session_graph_data/"),
        {
          project_id: project.id,
          filters: sessionFilter,
          interval: "day",
          property: "average",
          req_data_config: { type: "SYSTEM_METRIC", id: "session_count" },
        },
      );
      const graphPoints = asArray(graph?.data);
      assert(
        Array.isArray(graphPoints),
        "Session graph response missing data array.",
      );
      assert(
        graphPoints.some((point) => Number(point.value || 0) > 0),
        "Session graph did not include a non-zero point for the selected session.",
      );

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            {
              source_type: "trace_session",
              source_id: session.session_id,
            },
          ],
        },
      );
      assert(
        added?.added === 1,
        "Adding the observe session to the queue failed.",
      );

      const queueEntry = await findQueueEntryForSource(
        client,
        queue.id,
        session.session_id,
      );
      assert(
        queueEntry?.item?.id,
        "Queue for-source lookup did not return the created session queue item.",
      );
      cleanup.defer("delete observe session queue item", () =>
        client.delete(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: queueEntry.item.id },
          ),
          { okStatuses: [200, 204, 404] },
        ),
      );

      const queueItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              source_type: "trace_session",
              limit: 100,
              ordering: "-created_at",
            },
          },
        ),
      );
      assert(
        queueItems.some((item) => item.id === queueEntry.item.id),
        "Queue item list did not include the added observe session item.",
      );

      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: queueEntry.item.id },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        detail?.item?.id === queueEntry.item.id,
        "Queue annotate detail did not reload the added session item.",
      );
      assert(
        detail?.item?.source_type === "trace_session",
        "Queue annotate detail did not preserve trace_session source type.",
      );

      evidence.push({
        project_id: project.id,
        queue_id: queue.id,
        session_id: session.session_id,
        filtered_rows: filteredRows.length,
        filter_values: asArray(filterValues.values).length,
        graph_nonzero_points: graphPoints.filter(
          (point) => Number(point.value || 0) > 0,
        ).length,
        queue_item_id: queueEntry.item.id,
      });
    },
  },
  {
    id: "OBS-API-007",
    title: "Observe user list, filters, graph, metrics, and related records",
    tags: ["observe", "users", "safe", "data-roundtrip"],
    async run({ client, evidence }) {
      const baseFilters = [defaultObserveUserDateFilter()];
      const { project, user, baseTotal } = await resolveObserveProjectWithUsers(
        client,
        evidence,
        baseFilters,
      );
      assert(user?.user_id, "Observe user row did not include user_id.");
      assert(
        user?.end_user_id,
        "Observe user row did not include end_user_id.",
      );

      const userFilter = canonicalTextFilter("user_id", "equals", user.user_id);
      const scopedFilters = [...baseFilters, ...userFilter];
      const filteredUsers = await client.get(
        queryWithFilters(apiPath("/tracer/users/"), scopedFilters, {
          project_id: project.id,
          current_page_index: 0,
          page_size: 5,
        }),
      );
      const filteredRows = asArray(filteredUsers.table || filteredUsers);
      assert(
        filteredRows.some((row) => row.user_id === user.user_id),
        "User list filter did not return the selected user.",
      );
      assert(
        responseCount(filteredUsers) <= baseTotal,
        "Filtered user list returned more rows than the base user list.",
      );

      const searchedUsers = await client.get(
        queryWithFilters(apiPath("/tracer/users/"), baseFilters, {
          project_id: project.id,
          current_page_index: 0,
          page_size: 5,
          search: user.user_id,
        }),
      );
      assert(
        asArray(searchedUsers.table || searchedUsers).some(
          (row) => row.user_id === user.user_id,
        ),
        "User list search did not return the selected user.",
      );

      const sortedUsers = await client.get(
        queryWithFilters(apiPath("/tracer/users/"), baseFilters, {
          project_id: project.id,
          current_page_index: 0,
          page_size: 5,
          sort_params: JSON.stringify([
            { column_id: "last_active", direction: "desc" },
          ]),
        }),
      );
      assert(
        asArray(sortedUsers.table || sortedUsers).length > 0,
        "Sorted user list returned no rows.",
      );

      const graph = await client.post(
        apiPath("/tracer/project/get_users_aggregate_graph_data/"),
        {
          project_id: project.id,
          filters: scopedFilters,
          interval: "day",
          property: "average",
          req_data_config: { type: "SYSTEM_METRIC", id: "active_users" },
        },
      );
      const graphPoints = asArray(graph?.data);
      assert(
        Array.isArray(graphPoints),
        "User graph response missing data array.",
      );
      assert(
        graphPoints.some((point) => Number(point.value || 0) > 0),
        "User graph did not include a non-zero point for the selected user.",
      );

      const metrics = asArray(
        await client.post(apiPath("/tracer/project/get_user_metrics/"), {
          project_id: project.id,
          end_user_id: user.end_user_id,
          interval: "day",
          filters: baseFilters,
        }),
      );
      assert(metrics.length > 0, "User metrics endpoint returned no rows.");
      assert(
        metrics.some((row) => row.user_id === user.user_id),
        "User metrics endpoint did not return the selected user.",
      );

      const userDetailGraph = await client.post(
        apiPath("/tracer/project/get_user_graph_data/"),
        {
          interval: "day",
          filters: baseFilters,
        },
        {
          query: {
            project_id: project.id,
            end_user_id: user.end_user_id,
          },
        },
      );
      for (const key of [
        "session",
        "trace",
        "cost",
        "input_tokens",
        "output_tokens",
      ]) {
        const series = userDetailGraph?.[key];
        assert(
          Array.isArray(series) || Array.isArray(series?.data),
          `User detail graph response omitted ${key} series.`,
        );
      }

      const relatedTraces = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/list_traces_of_session/"),
          scopedFilters,
          {
            project_id: project.id,
            page_number: 0,
            page_size: 5,
          },
        ),
      );
      const traceRows = asArray(relatedTraces);
      assert(traceRows.length > 0, "User-scoped trace list returned no rows.");
      assert(
        traceRows.some((row) => rowContainsValue(row, user.user_id)),
        "User-scoped trace list did not include the selected user.",
      );

      const relatedSessions = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace-session/list_sessions/"),
          scopedFilters,
          {
            project_id: project.id,
            page_number: 0,
            page_size: 5,
          },
        ),
      );
      const sessionRows = asArray(relatedSessions);
      assert(
        sessionRows.length > 0,
        "User-scoped session list returned no rows.",
      );
      assert(
        sessionRows.some((row) => rowContainsValue(row, user.user_id)),
        "User-scoped session list did not include the selected user.",
      );

      const crossProjectUser = await client.get(
        queryWithFilters(apiPath("/tracer/users/"), scopedFilters, {
          current_page_index: 0,
          page_size: 5,
        }),
      );
      assert(
        asArray(crossProjectUser.table || crossProjectUser).some(
          (row) => row.user_id === user.user_id,
        ),
        "Cross-project user detail source query did not return the selected user.",
      );

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        user_id: user.user_id,
        end_user_id: user.end_user_id,
        date_filter_days: 90,
        base_total: baseTotal,
        filtered_total: responseCount(filteredUsers),
        search_total: responseCount(searchedUsers),
        graph_points: graphPoints.length,
        graph_nonzero_points: graphPoints.filter(
          (point) => Number(point.value || 0) > 0,
        ).length,
        metrics_rows: metrics.length,
        user_detail_graph_keys: Object.keys(userDetailGraph),
        related_traces: responseCount(relatedTraces),
        related_sessions: responseCount(relatedSessions),
      });
    },
  },
  {
    id: "OBS-API-008",
    title:
      "Observe voice call list, detail, direct annotation, and default queue item",
    tags: ["observe", "voice", "annotation", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const { project, call, detail, rootSpanId, queue, label, baseRows } =
        await resolveObserveVoiceCallForAnnotation(client, evidence);
      const traceId = call.trace_id;
      assert(traceId, "Voice call row did not include trace_id.");
      assert(
        detail?.trace_id === traceId || detail?.id === traceId,
        "Voice call detail did not return the selected trace.",
      );
      assert(
        Array.isArray(detail?.observation_span) &&
          detail.observation_span.some((span) => span.id === rootSpanId),
        "Voice call detail did not include the selected root conversation span.",
      );

      const traceFilter = canonicalTextFilter("trace_id", "equals", traceId);
      const filteredList = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/list_voice_calls/"),
          traceFilter,
          {
            project_id: project.id,
            page: 1,
            page_size: 5,
          },
        ),
      );
      const filteredRows = asArray(filteredList);
      assert(
        filteredRows.some((row) => row.trace_id === traceId),
        "Voice call trace_id filter did not return the selected call.",
      );
      assert(
        filteredRows.length <= baseRows.length,
        "Filtered voice call list returned more rows than the base list.",
      );

      const labelNote = `OBS-005 label note ${runId}`;
      const itemNote = `OBS-005 item note ${runId}`;
      const scoreValue = scoreValueForVoiceLabel(label, runId);
      const created = await client.post(apiPath("/model-hub/scores/bulk/"), {
        source_type: "trace",
        source_id: traceId,
        scores: [
          {
            label_id: label.id,
            value: scoreValue,
            notes: labelNote,
          },
        ],
        notes: "",
        span_notes: itemNote,
        span_notes_source_id: rootSpanId,
      });

      const createdScores = asArray(created?.scores);
      assert(
        asArray(created?.errors).length === 0,
        `Direct voice annotation returned errors: ${JSON.stringify(created.errors)}`,
      );
      assert(
        createdScores.length === 1,
        "Direct voice annotation did not save one score.",
      );
      const createdScore = createdScores[0];
      const queueItemId = createdScore.queue_item;
      assert(
        queueItemId,
        "Direct voice annotation did not create a queue item.",
      );
      assert(
        createdScore.source_type === "trace",
        "Direct voice annotation did not save as trace source_type.",
      );
      assert(
        createdScore.source_id === traceId,
        "Direct voice annotation score source_id did not match the voice trace.",
      );
      assert(
        valuesEqual(createdScore.value, scoreValue),
        "Direct voice annotation score value did not round-trip.",
      );
      assert(
        createdScore.notes === labelNote,
        "Direct voice annotation label note did not persist.",
      );

      cleanup.defer(
        "delete observe voice direct annotation artifacts",
        async () => {
          await client.post(
            apiPath("/model-hub/scores/bulk/"),
            {
              source_type: "trace",
              source_id: traceId,
              queue_item_id: queueItemId,
              scores: [
                {
                  label_id: label.id,
                  value: scoreValue,
                  notes: "",
                },
              ],
              notes: "",
              span_notes: "",
              span_notes_source_id: rootSpanId,
            },
            { okStatuses: [200, 400, 404] },
          );
          for (const score of createdScores) {
            if (!score?.id) continue;
            await client.delete(
              apiPath("/model-hub/scores/{id}/", { id: score.id }),
              {
                okStatuses: [200, 204, 404],
              },
            );
          }
          await client.delete(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/items/{id}/",
              queue.id,
              { id: queueItemId },
            ),
            { okStatuses: [200, 204, 404] },
          );
        },
      );

      const queueEntry = (
        await getVoiceQueueEntriesForSource(client, traceId, rootSpanId)
      ).find((entry) => entry?.queue?.id === queue.id);
      assert(
        queueEntry?.item?.id === queueItemId,
        "Default queue item did not reload.",
      );
      assert(
        queueEntry.item.source_type === "trace",
        "Default queue item did not preserve trace source_type.",
      );
      assert(
        queueEntry.item.source_id === traceId,
        "Default queue item source_id did not match the voice trace.",
      );
      assert(
        valuesEqual(queueEntry.existing_scores?.[label.id], scoreValue),
        "Default queue entry did not prefill the saved label value.",
      );
      assert(
        queueEntry.existing_label_notes?.[label.id] === labelNote,
        "Default queue entry did not prefill the saved label note.",
      );
      assert(
        queueEntry.existing_notes === itemNote,
        "Default queue entry did not prefill the whole-item note.",
      );
      assert(
        asArray(queueEntry.span_notes).some((note) => note.notes === itemNote),
        "Default queue entry did not include the persisted whole-item note.",
      );

      const sourceScores = asArray(
        await client.get(apiPath("/model-hub/scores/for-source/"), {
          query: { source_type: "trace", source_id: traceId },
        }),
      );
      assert(
        sourceScores.some(
          (score) =>
            score.id === createdScore.id &&
            score.queue_item === queueItemId &&
            score.source_type === "trace",
        ),
        "Scores for-source did not include the direct voice annotation.",
      );

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        voice_rows: baseRows.length,
        trace_id: traceId,
        root_span_id: rootSpanId,
        queue_id: queue.id,
        queue_name: queue.name || null,
        queue_item_id: queueItemId,
        label_id: label.id,
        label_name: label.name,
        label_type: label.type,
        filtered_rows: filteredRows.length,
        detail_observation_spans: detail.observation_span.length,
        transcript_messages:
          asArray(detail.messages).length || asArray(detail.transcript).length,
      });
    },
  },
  {
    id: "OBS-API-009",
    title:
      "Observe filter property inventory and value lookup across row types",
    tags: ["observe", "filters", "safe", "metadata", "values"],
    async run({ client, evidence }) {
      const coverage = await resolveObserveFilterCoverage(client, evidence);
      const {
        project,
        metrics,
        customAttribute,
        customAttributeValues,
        annotationMetric,
        annotationValues,
        evalMetric,
        statusValues,
        sessionValues,
        userValues,
      } = coverage;

      const metricNames = new Set(metrics.map((metric) => metric?.name));
      assert(
        metricNames.has("latency"),
        "Metrics catalog must include latency.",
      );
      assert(metricNames.has("status"), "Metrics catalog must include status.");
      assert(
        customAttribute?.name,
        "Metrics catalog did not expose a custom attribute.",
      );
      assert(
        annotationMetric?.name,
        "Metrics catalog did not expose an annotation metric.",
      );
      assert(
        evalMetric?.name,
        "Metrics catalog did not expose an eval metric.",
      );

      const spanFields = asArray(
        await client.get(
          apiPath("/tracer/observation-span/get_observation_span_fields/"),
          {
            query: {
              filters: { project_id: project.id },
              row_type: "spans",
            },
          },
        ),
      );
      assert(
        spanFields.some((field) => field?.name === "latency_ms"),
        "Observation span fields must include latency_ms.",
      );
      assert(
        spanFields.some((field) => field?.name === "child_spans"),
        "Observation span fields must include child_spans virtual field.",
      );

      const spanAttributes = asArray(
        await client.get(
          apiPath("/tracer/observation-span/get_span_attributes_list/"),
          {
            query: {
              filters: { project_id: project.id },
              row_type: "spans",
            },
          },
        ),
      );
      assert(
        spanAttributes.includes(customAttribute.name),
        "Span attribute inventory did not include the selected custom attribute.",
      );
      assertNoStringifiedPaths(spanAttributes, "span attribute inventory");
      assertNoNormalizedDuplicateKeys(
        spanAttributes,
        "span attribute inventory",
      );

      const evalAttributeCounts = {};
      for (const rowType of ["spans", "traces", "sessions", "voiceCalls"]) {
        const paths = asArray(
          await client.get(
            apiPath("/tracer/observation-span/get_eval_attributes_list/"),
            {
              query: {
                filters: { project_id: project.id },
                row_type: rowType,
              },
            },
          ),
        );
        assert(paths.length > 0, `${rowType} eval attribute list was empty.`);
        assertNoStringifiedPaths(paths, `${rowType} eval attribute list`);
        evalAttributeCounts[rowType] = paths.length;

        if (rowType === "spans" || rowType === "voiceCalls") {
          assert(
            paths.includes(customAttribute.name),
            `${rowType} eval attributes did not expose the raw custom attribute.`,
          );
        }
        if (rowType === "traces") {
          assert(
            paths.includes("input"),
            "Trace eval attributes missing input.",
          );
          assert(
            paths.some((path) => path.endsWith(`.${customAttribute.name}`)),
            "Trace eval attributes did not include indexed span custom attributes.",
          );
        }
        if (rowType === "sessions") {
          assert(
            paths.includes("name"),
            "Session eval attributes missing name.",
          );
          assert(
            paths.some((path) => path.includes(".spans.")),
            "Session eval attributes did not include nested trace/span paths.",
          );
        }
      }

      const selectedSessionValue = valueOfOption(sessionValues[0]);
      const searchedSessionValues = await client.get(
        apiPath("/tracer/trace-session/get_session_filter_values/"),
        {
          query: {
            project_id: project.id,
            column: "session_id",
            search: selectedSessionValue.slice(0, 8),
            page: 0,
            page_size: 20,
          },
        },
      );
      assert(
        asArray(searchedSessionValues.values).some(
          (value) => String(value) === selectedSessionValue,
        ),
        "Session filter value search did not include the selected session id.",
      );

      assert(
        statusValues.some((option) => valueOfOption(option) === "OK"),
        "Status filter values must include OK.",
      );
      assert(
        customAttributeValues.length > 0,
        "Custom attribute filter values were empty.",
      );
      assert(
        annotationValues.length > 0 ||
          asArray(annotationMetric.choices).length > 0,
        "Annotation filter values/choices were empty.",
      );
      assert(
        asArray(evalMetric.choices).length > 0 || evalMetric.output_type,
        "Eval metric must expose choices or an output type for the picker.",
      );
      assert(userValues.length > 0, "Session user_id value lookup was empty.");

      const voiceCoverage = await resolveObserveVoiceFilterSample(
        client,
        evidence,
      );
      const voiceTurnFilter = canonicalNumberFilter(
        "turn_count",
        "greater_than_or_equal",
        Number(voiceCoverage.call.turn_count || 0),
      );
      const voiceTurnRows = asArray(
        await client.get(
          queryWithFilters(
            apiPath("/tracer/trace/list_voice_calls/"),
            voiceTurnFilter,
            {
              project_id: voiceCoverage.project.id,
              page: 1,
              page_size: 5,
            },
          ),
        ),
      );
      assert(
        voiceTurnRows.length <= 5,
        "Voice call list returned more rows than requested page_size.",
      );
      assert(
        voiceTurnRows.length > 0,
        "Voice turn_count filter returned no rows.",
      );

      const reasonPrefix = String(voiceCoverage.call.ended_reason || "").slice(
        0,
        8,
      );
      if (reasonPrefix) {
        const voiceReasonRows = asArray(
          await client.get(
            queryWithFilters(
              apiPath("/tracer/trace/list_voice_calls/"),
              canonicalTextFilter("ended_reason", "contains", reasonPrefix),
              {
                project_id: voiceCoverage.project.id,
                page: 1,
                page_size: 5,
              },
            ),
          ),
        );
        assert(
          voiceReasonRows.length <= 5,
          "Voice ended_reason filter returned more rows than requested page_size.",
        );
        assert(
          voiceReasonRows.some((row) =>
            String(row.ended_reason || "").includes(reasonPrefix),
          ),
          "Voice ended_reason filter did not return a matching call.",
        );
      }

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        metric_count: metrics.length,
        custom_attribute: customAttribute.name,
        custom_attribute_value: valueOfOption(customAttributeValues[0]),
        annotation_metric: metricLabel(annotationMetric),
        annotation_value:
          valueOfOption(annotationValues[0]) ||
          valueOfOption(asArray(annotationMetric.choices)[0]),
        eval_metric: metricLabel(evalMetric),
        eval_choices: asArray(evalMetric.choices).length,
        status_values: statusValues.map(valueOfOption),
        session_value: selectedSessionValue,
        user_value: valueOfOption(userValues[0]),
        span_attribute_count: spanAttributes.length,
        eval_attribute_counts: evalAttributeCounts,
        voice_project_id: voiceCoverage.project.id,
        voice_trace_id: voiceCoverage.call.trace_id,
        voice_turn_count: voiceCoverage.call.turn_count,
        voice_ended_reason: voiceCoverage.call.ended_reason || null,
      });
    },
  },
  {
    id: "OBS-API-010",
    title: "Observe span annotation and eval feedback drawer readbacks",
    tags: ["observe", "annotations", "feedback", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const {
        project,
        span,
        evalConfigId,
        evaluationDetail,
        queue: existingQueue,
      } = await resolveObserveSpanWithEvalForFeedback(client, evidence);
      const spanId = span.span_id || span.id;

      let queue = existingQueue;
      if (!queue?.id) {
        let defaultQueuePayload;
        try {
          defaultQueuePayload = await client.post(
            apiPath("/model-hub/annotation-queues/get-or-create-default/"),
            { project_id: project.id },
          );
        } catch (error) {
          if (error?.status === 402) {
            skip(
              "No existing default queue is available for the eval span project, and local queue quota blocks creating one.",
            );
          }
          throw error;
        }
        queue = defaultQueuePayload?.queue || defaultQueuePayload;
      }
      assert(
        queue?.id,
        "Default annotation queue response did not include queue id.",
      );

      const queueEntriesBefore = await getObservationSpanQueueEntries(
        client,
        spanId,
      );
      const defaultEntryBefore = queueEntriesBefore.find(
        (entry) => String(entry?.queue?.id) === String(queue.id),
      );
      const preexistingQueueItemId = defaultEntryBefore?.item?.id || null;

      const labelName = `OBS-007 legacy drawer ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Temporary label for observe drawer API journey.",
          settings: {
            placeholder: "Observe drawer journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : await findAnnotationLabelByName(client, labelName);
      const labelId = createdLabel?.id;
      assert(
        labelId,
        "Temporary observe annotation label create did not return id.",
      );
      cleanup.defer("delete OBS-007 temporary label", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
          ),
        ),
      );

      const addedLabel = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/add-label/", {
          id: queue.id,
        }),
        { label_id: labelId, required: false },
      );
      assert(
        String(addedLabel?.label?.id) === String(labelId),
        "Default queue add-label did not return the temporary label.",
      );
      cleanup.defer("remove OBS-007 temporary label from default queue", () =>
        ignoreNotFound(() =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
              id: queue.id,
            }),
            { label_id: labelId },
            { okStatuses: [200, 400, 404] },
          ),
        ),
      );

      const annotationText = `OBS-007 legacy span annotation ${runId}`;
      const spanNote = `OBS-007 legacy span note ${runId}`;
      const expectedScoreValue = { text: annotationText };
      const annotationResult = await client.post(
        apiPath("/tracer/observation-span/add_annotations/"),
        {
          observation_span_id: spanId,
          annotation_values: { [labelId]: annotationText },
          notes: spanNote,
        },
      );
      assert(
        asArray(annotationResult?.failed_labels).length === 0,
        `Legacy add_annotations returned failures: ${JSON.stringify(
          annotationResult?.failed_labels,
        )}`,
      );
      assert(
        asArray(annotationResult?.success_labels)
          .map(String)
          .includes(String(labelId)),
        "Legacy add_annotations did not report the temporary label as saved.",
      );

      const sourceScores = asArray(
        await client.get(apiPath("/model-hub/scores/for-source/"), {
          query: { source_type: "observation_span", source_id: spanId },
        }),
      );
      const createdScore = sourceScores.find(
        (score) =>
          String(scoreLabelId(score)) === String(labelId) &&
          valuesEqual(score.value, expectedScoreValue),
      );
      assert(
        createdScore?.id,
        "Scores for-source did not include legacy annotation.",
      );
      const queueItemId =
        createdScore.queue_item?.id ||
        createdScore.queue_item ||
        createdScore.queue_item_id;
      assert(
        queueItemId,
        "Legacy annotation score did not attach to a queue item.",
      );

      if (!preexistingQueueItemId) {
        cleanup.defer("delete OBS-007 temporary default queue item", () =>
          ignoreNotFound(() =>
            client.delete(
              queuePath(
                "/model-hub/annotation-queues/{queue_id}/items/{id}/",
                queue.id,
                { id: queueItemId },
              ),
              { okStatuses: [200, 204, 404] },
            ),
          ),
        );
      }
      cleanup.defer(
        "delete OBS-007 legacy annotation score and note",
        async () => {
          await client.post(
            apiPath("/model-hub/scores/bulk/"),
            {
              source_type: "observation_span",
              source_id: spanId,
              queue_item_id: queueItemId,
              scores: [
                { label_id: labelId, value: expectedScoreValue, notes: "" },
              ],
              span_notes: "",
              span_notes_source_id: spanId,
            },
            { okStatuses: [200, 400, 404] },
          );
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/scores/{id}/", { id: createdScore.id }),
              {
                okStatuses: [200, 204, 404],
              },
            ),
          );
        },
      );

      const queueEntry = (
        await getObservationSpanQueueEntries(client, spanId)
      ).find((entry) => String(entry?.queue?.id) === String(queue.id));
      assert(
        queueEntry?.item?.id,
        "Default queue entry did not reload for span.",
      );
      assert(
        valuesEqual(queueEntry.existing_scores?.[labelId], expectedScoreValue),
        "Default queue entry did not prefill the legacy annotation value.",
      );
      assert(
        queueEntry.existing_label_notes?.[labelId] === spanNote,
        "Default queue entry did not prefill the legacy annotation note.",
      );
      assert(
        queueEntry.existing_notes === spanNote ||
          asArray(queueEntry.span_notes).some(
            (note) => note.notes === spanNote,
          ),
        "Default queue entry did not expose the legacy span note.",
      );

      assert(
        evaluationDetail?.score !== undefined || evaluationDetail?.explanation,
        "Evaluation detail did not return score or explanation.",
      );
      const feedbackValue = "0.42";
      const feedbackExplanation = `OBS-007 feedback explanation ${runId}`;
      const feedbackImprovement = `OBS-007 feedback improvement ${runId}`;
      const createdFeedback = await client.post(
        apiPath("/tracer/observation-span/submit_feedback/"),
        {
          observation_span_id: spanId,
          custom_eval_config_id: evalConfigId,
          feedback_value: feedbackValue,
          feedback_explanation: feedbackExplanation,
          feedback_improvement: feedbackImprovement,
        },
      );
      const feedbackId = createdFeedback?.feedback_id || createdFeedback?.id;
      assert(feedbackId, "submit_feedback did not return feedback_id.");
      cleanup.defer("delete OBS-007 temporary feedback", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
            {
              okStatuses: [200, 204, 404],
            },
          ),
        ),
      );

      const feedbackAction = await client.post(
        apiPath("/tracer/observation-span/submit_feedback_action_type/"),
        {
          observation_span_id: spanId,
          custom_eval_config_id: evalConfigId,
          feedback_id: feedbackId,
          action_type: "retune",
        },
      );
      assert(
        String(feedbackAction?.message || "").includes("successfully"),
        "submit_feedback_action_type did not confirm success.",
      );

      const feedbackDetail = await client.get(
        apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
      );
      assert(
        String(feedbackDetail?.id) === String(feedbackId),
        "Feedback detail did not return the created feedback.",
      );
      assert(
        feedbackDetail.source_id === spanId &&
          feedbackDetail.custom_eval_config_id === evalConfigId &&
          feedbackDetail.value === feedbackValue &&
          feedbackDetail.action_type === "retune",
        "Feedback detail did not preserve the drawer feedback fields.",
      );

      await client.delete(
        apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
        {
          okStatuses: [200, 204],
        },
      );
      await ignoreNotFound(() =>
        client.get(apiPath("/model-hub/feedback/{id}/", { id: feedbackId }), {
          okStatuses: [404],
        }),
      );

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        span_id: spanId,
        trace_id: span.trace_id || null,
        custom_eval_config_id: evalConfigId,
        queue_id: queue.id,
        queue_item_id: queueItemId,
        label_id: labelId,
        score_id: createdScore.id,
        feedback_id: feedbackId,
      });
    },
  },
  {
    id: "OBS-API-011",
    title:
      "Observe project list, search, tag filter, detail, and SDK code readback",
    tags: ["observe", "projects", "safe", "data-integrity"],
    async run({ client, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const list = await client.get(apiPath("/tracer/project/list_projects/"), {
        query: {
          project_type: "observe",
          page_number: 0,
          page_size: 25,
          sort_by: "updated_at",
          sort_direction: "desc",
        },
      });
      const projects = asArray(list);
      assert(projects.length > 0, "Observe project list returned no projects.");
      assertObserveProjectListPayload(list, projects);

      const audit = await loadObserveProjectListDbAudit({
        organizationId,
        workspaceId,
      });
      assert(
        Number(list.metadata.total_rows) ===
          Number(audit.visible_observe_project_count),
        "Observe project list total_rows did not match workspace-scoped DB audit.",
      );

      let selectedProject = null;
      let selectedDetail = null;
      for (const project of projects.slice(0, 12)) {
        const detail = await client.get(
          apiPath("/tracer/project/{id}/", { id: project.id }),
        );
        if (
          detail?.trace_type === "observe" &&
          detail?.workspace === workspaceId
        ) {
          selectedProject = project;
          selectedDetail = detail;
          break;
        }
      }
      assert(
        selectedProject,
        "Observe list did not include a current-workspace project on the first page.",
      );
      assertObserveProjectDetail(selectedDetail, {
        projectId: selectedProject.id,
        organizationId,
        workspaceId,
      });

      const searchTerm = String(selectedProject.name || "").slice(0, 8);
      const search = await client.get(
        apiPath("/tracer/project/list_projects/"),
        {
          query: {
            project_type: "observe",
            name: searchTerm,
            page_number: 0,
            page_size: 25,
          },
        },
      );
      assert(
        asArray(search).some((project) => project.id === selectedProject.id),
        "Observe project search did not return the selected project.",
      );

      const nameAsc = await client.get(
        apiPath("/tracer/project/list_projects/"),
        {
          query: {
            project_type: "observe",
            page_number: 0,
            page_size: 25,
            sort_by: "name",
            sort_direction: "asc",
          },
        },
      );
      assertSortedObserveProjectNames(asArray(nameAsc), "asc");

      const issuesSort = await client.get(
        apiPath("/tracer/project/list_projects/"),
        {
          query: {
            project_type: "observe",
            page_number: 0,
            page_size: 10,
            sort_by: "issues",
            sort_direction: "desc",
          },
        },
      );
      assert(
        asArray(issuesSort).length > 0,
        "Observe project list returned no rows when sorting by synthetic issues.",
      );

      let tagFilterCount = 0;
      const tagProject = projects.find(
        (project) => asArray(project.tags).length > 0,
      );
      if (tagProject) {
        const tag = tagProject.tags[0];
        const tagFilter = await client.get(
          apiPath("/tracer/project/list_projects/"),
          {
            query: {
              project_type: "observe",
              tags: tag,
              page_number: 0,
              page_size: 25,
            },
          },
        );
        const tagRows = asArray(tagFilter);
        assert(
          tagRows.some((project) => project.id === tagProject.id),
          "Observe project tag filter did not include the tagged project.",
        );
        assert(
          tagRows.every((project) => asArray(project.tags).includes(tag)),
          "Observe project tag filter returned a project without the requested tag.",
        );
        tagFilterCount = tagRows.length;
      }

      const sdkCode = await client.get(
        apiPath("/tracer/project/project_sdk_code/"),
        {
          query: { project_type: "observe" },
        },
      );
      assertObserveSdkCodeUsesPlaceholders(sdkCode);

      evidence.push({
        project_id: selectedProject.id,
        project_name: selectedProject.name,
        project_count: list.metadata.total_rows,
        page_rows: projects.length,
        db_workspace_project_count: audit.workspace_observe_project_count,
        db_null_workspace_project_count:
          audit.null_workspace_observe_project_count,
        db_other_workspace_project_count:
          audit.other_workspace_observe_project_count,
        search_count: asArray(search).length,
        tag_filter_count: tagFilterCount,
        issues_sort_rows: asArray(issuesSort).length,
        sdk_code_uses_placeholders: true,
      });
    },
  },
  {
    id: "OBS-API-012",
    title:
      "Dashboard and widget lifecycle, query guards, metrics, and chart graph",
    tags: ["observe", "dashboards", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const project = await resolveObserveProject(client, evidence);
      const dashboardName = `api journey dashboard ${runId}`;
      const dashboardNameUpdated = `${dashboardName} updated`;
      const dashboardDescription =
        "Temporary dashboard created by API journey.";
      const queryConfig = dashboardQueryConfig(project.id);
      let dashboardId = null;
      let dashboardDeleted = false;
      let mainWidgetId = null;
      let duplicateWidgetId = null;
      let emptyWidgetId = null;
      let emptyWidgetDeleted = false;

      const metricsCatalog = await client.get(
        apiPath("/tracer/dashboard/metrics/"),
        {
          query: { project_ids: project.id },
        },
      );
      const metrics = asArray(metricsCatalog?.metrics);
      assert(
        metrics.some((metric) => metric.name === "latency"),
        "Dashboard metrics catalog did not include latency.",
      );
      assert(
        metrics.some((metric) => metric.name === "cost"),
        "Dashboard metrics catalog did not include cost.",
      );

      const simulationAgents = await client.get(
        apiPath("/tracer/dashboard/simulation-agents/"),
      );
      assert(
        Array.isArray(simulationAgents?.agents),
        "Dashboard simulation-agents response must include agents array.",
      );

      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.post(apiPath("/tracer/dashboard/"), {
            name: "",
            description: "invalid blank dashboard",
          }),
        "Dashboard create",
      );

      const created = await client.post(apiPath("/tracer/dashboard/"), {
        name: dashboardName,
        description: dashboardDescription,
      });
      dashboardId = created?.id;
      assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");
      cleanup.defer("delete OBS-API-012 dashboard", () => {
        if (dashboardDeleted || !dashboardId) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
          ),
        );
      });
      assertDashboardPayload(created, {
        dashboardId,
        name: dashboardName,
        description: dashboardDescription,
        workspaceId,
      });

      const list = asArray(await client.get(apiPath("/tracer/dashboard/")));
      const listed = list.find((dashboard) => dashboard.id === dashboardId);
      assert(listed, "Dashboard list did not include the created dashboard.");
      assert(
        listed.widget_count === 0,
        "Created dashboard should start with widget_count=0.",
      );

      const detail = await client.get(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
      assertDashboardPayload(detail, {
        dashboardId,
        name: dashboardName,
        description: dashboardDescription,
        workspaceId,
      });
      assert(
        asArray(detail.widgets).length === 0,
        "Created dashboard detail should have no widgets.",
      );

      const patched = await client.patch(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
        { name: dashboardNameUpdated },
      );
      assert(
        patched.name === dashboardNameUpdated &&
          patched.description === dashboardDescription,
        "Dashboard PATCH did not preserve/update expected fields.",
      );

      const putName = `${dashboardName} put`;
      const putDescription = "Dashboard PUT description";
      const put = await client.put(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
        { name: putName, description: putDescription },
      );
      assert(
        put.name === putName && put.description === putDescription,
        "Dashboard PUT did not persist expected fields.",
      );

      const mainWidget = await client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
          dashboard_pk: dashboardId,
        }),
        {
          name: "Latency widget",
          description: "Temporary latency widget",
          position: 0,
          width: 6,
          height: 4,
          query_config: queryConfig,
          chart_config: { chart_type: "line" },
        },
      );
      mainWidgetId = mainWidget?.id;
      assertWidgetPayload(mainWidget, {
        widgetId: mainWidgetId,
        name: "Latency widget",
        width: 6,
        chartType: "line",
      });

      const emptyWidget = await client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
          dashboard_pk: dashboardId,
        }),
        {
          name: "Empty query widget",
          position: 1,
          width: 6,
          height: 4,
          query_config: {},
          chart_config: { chart_type: "metric" },
        },
      );
      emptyWidgetId = emptyWidget?.id;
      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.post(
            apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/query/", {
              dashboard_pk: dashboardId,
              id: emptyWidgetId,
            }),
          ),
        "Widget query without metrics",
      );
      await client.delete(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
          dashboard_pk: dashboardId,
          id: emptyWidgetId,
        }),
      );
      emptyWidgetDeleted = true;

      const widgetsAfterCreate = asArray(
        await client.get(
          apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
            dashboard_pk: dashboardId,
          }),
        ),
      );
      assert(
        widgetsAfterCreate.some((widget) => widget.id === mainWidgetId),
        "Widget list did not include the created latency widget.",
      );
      assert(
        !widgetsAfterCreate.some((widget) => widget.id === emptyWidgetId),
        "Widget list still included the deleted empty widget.",
      );

      const widgetDetail = await client.get(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
          dashboard_pk: dashboardId,
          id: mainWidgetId,
        }),
      );
      assertWidgetPayload(widgetDetail, {
        widgetId: mainWidgetId,
        name: "Latency widget",
        width: 6,
        chartType: "line",
      });

      const patchedWidget = await client.patch(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
          dashboard_pk: dashboardId,
          id: mainWidgetId,
        }),
        {
          name: "Latency widget patched",
          width: 8,
          height: 5,
          chart_config: { chart_type: "bar" },
        },
      );
      assertWidgetPayload(patchedWidget, {
        widgetId: mainWidgetId,
        name: "Latency widget patched",
        width: 8,
        chartType: "bar",
      });

      const duplicate = await client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/duplicate/", {
          dashboard_pk: dashboardId,
          id: mainWidgetId,
        }),
      );
      duplicateWidgetId = duplicate?.id;
      assert(
        isUuid(duplicateWidgetId),
        "Widget duplicate did not return a UUID id.",
      );
      assert(
        duplicate.name === "Latency widget patched (Copy)",
        "Widget duplicate did not use the expected copy name.",
      );

      await client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/reorder/", {
          dashboard_pk: dashboardId,
        }),
        {
          order: [
            { id: duplicateWidgetId, width: 4 },
            { id: mainWidgetId, width: 8 },
          ],
        },
      );
      const reorderedDetail = await client.get(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
      const reorderedWidgets = asArray(reorderedDetail.widgets);
      const reorderedDuplicate = reorderedWidgets.find(
        (widget) => widget.id === duplicateWidgetId,
      );
      const reorderedMain = reorderedWidgets.find(
        (widget) => widget.id === mainWidgetId,
      );
      assert(
        reorderedDuplicate?.position === 0 && reorderedDuplicate?.width === 4,
        "Widget reorder did not move/resize the duplicate widget.",
      );
      assert(
        reorderedMain?.position === 1 && reorderedMain?.width === 8,
        "Widget reorder did not move/resize the main widget.",
      );

      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.post(
            apiPath("/tracer/dashboard/{dashboard_pk}/widgets/preview/", {
              dashboard_pk: dashboardId,
            }),
            { query_config: {} },
          ),
        "Dashboard widget preview",
      );
      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.post(apiPath("/tracer/dashboard/query/"), {
            ...queryConfig,
            project_ids: [randomUUID()],
          }),
        "Dashboard query with an inaccessible project",
      );

      const chartGraph = await client.get(
        apiPath("/tracer/charts/fetch_graph/"),
        {
          query: {
            project_id: project.id,
            interval: "day",
            property: "average",
            filters: [],
            req_data_config: { id: "latency", type: "SYSTEM_METRIC" },
          },
        },
      );
      assert(
        chartGraph?.metric_name === "latency" ||
          Array.isArray(chartGraph?.data),
        "Charts fetch_graph did not return the single system metric shape.",
      );
      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.get(apiPath("/tracer/charts/fetch_graph/"), {
            query: {
              project_id: randomUUID(),
              interval: "day",
              property: "average",
              filters: [],
              req_data_config: { id: "latency", type: "SYSTEM_METRIC" },
            },
          }),
        "Charts fetch_graph with inaccessible project",
      );

      const activeAudit = await loadDashboardDbAudit({
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [mainWidgetId, duplicateWidgetId, emptyWidgetId],
      });
      assertDashboardDbAudit(activeAudit, {
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [mainWidgetId, duplicateWidgetId, emptyWidgetId],
        expectedDashboardDeleted: false,
        expectedActiveWidgetCount: 2,
      });

      await client.delete(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
      dashboardDeleted = true;

      const deletedAudit = await loadDashboardDbAudit({
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [mainWidgetId, duplicateWidgetId, emptyWidgetId],
      });
      assertDashboardDbAudit(deletedAudit, {
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [mainWidgetId, duplicateWidgetId, emptyWidgetId],
        expectedDashboardDeleted: true,
        expectedActiveWidgetCount: 0,
      });

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        dashboard_id: dashboardId,
        widget_id: mainWidgetId,
        duplicate_widget_id: duplicateWidgetId,
        empty_widget_deleted_by_public_endpoint: emptyWidgetDeleted,
        dashboard_deleted_at_set: deletedAudit.dashboard_deleted_at_set,
        active_widgets_after_dashboard_delete: deletedAudit.active_widget_count,
        metrics_count: metrics.length,
        simulation_agent_count: asArray(simulationAgents.agents).length,
        chart_metric_name: chartGraph?.metric_name || "latency",
      });
    },
  },
  {
    id: "OBS-API-020",
    title: "Observe charts dashboard system metrics and date range filters",
    tags: ["observe", "charts", "safe", "read-only"],
    async run({ client, evidence }) {
      const project = await resolveObserveProject(client, evidence);

      const systemMetrics = asArray(
        await client.get(apiPath("/tracer/project/fetch_system_metrics/")),
      );
      for (const metric of ["latency", "tokens", "cost"]) {
        assert(
          systemMetrics.includes(metric),
          `System metric inventory omitted ${metric}.`,
        );
      }

      const thirtyDayFilter = observeChartsDateFilter(30);
      const sevenDayFilter = observeChartsDateFilter(7);
      const thirtyDayGraph = await client.get(
        apiPath("/tracer/project/get_graph_data/"),
        {
          query: {
            project_id: project.id,
            interval: "day",
            filters: JSON.stringify([thirtyDayFilter]),
          },
        },
      );
      const sevenDayGraph = await client.get(
        apiPath("/tracer/project/get_graph_data/"),
        {
          query: {
            project_id: project.id,
            interval: "day",
            filters: JSON.stringify([sevenDayFilter]),
          },
        },
      );
      const sevenDayHourlyGraph = await client.get(
        apiPath("/tracer/project/get_graph_data/"),
        {
          query: {
            project_id: project.id,
            interval: "hour",
            filters: JSON.stringify([sevenDayFilter]),
          },
        },
      );

      const thirtyDaySummary = assertObserveChartsGraph(thirtyDayGraph, "30D");
      const sevenDaySummary = assertObserveChartsGraph(sevenDayGraph, "7D");
      const sevenDayHourlySummary = assertObserveChartsGraph(
        sevenDayHourlyGraph,
        "7D hourly",
      );
      assert(
        sevenDaySummary.traffic_points <= thirtyDaySummary.traffic_points,
        "7D chart range returned more daily traffic buckets than 30D.",
      );
      assert(
        sevenDayHourlySummary.traffic_points >= sevenDaySummary.traffic_points,
        "Hourly chart range returned fewer buckets than daily range.",
      );

      const latencyFetchGraph = await client.get(
        apiPath("/tracer/charts/fetch_graph/"),
        {
          query: {
            project_id: project.id,
            interval: "day",
            property: "average",
            filters: [],
            req_data_config: { id: "latency", type: "SYSTEM_METRIC" },
          },
        },
      );
      assert(
        latencyFetchGraph?.metric_name === "latency" ||
          Array.isArray(latencyFetchGraph?.data),
        "Charts fetch_graph latency payload did not return the expected shape.",
      );

      const evalNames = asArray(
        await client.get(apiPath("/tracer/trace/get_eval_names/"), {
          query: { project_id: project.id },
        }),
      );

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        system_metric_count: systemMetrics.length,
        thirty_day_points: thirtyDaySummary,
        seven_day_points: sevenDaySummary,
        seven_day_hourly_points: sevenDayHourlySummary,
        fetch_graph_metric_name: latencyFetchGraph?.metric_name || "latency",
        evaluation_metric_count: evalNames.length,
      });
    },
  },
  {
    id: "OBS-API-019",
    title:
      "Dashboard widget full PUT route persists replacement and scope guards",
    tags: [
      "observe",
      "dashboards",
      "widgets",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const project = await resolveObserveProject(client, evidence);
      const dashboardName = `api journey widget put ${runId}`;
      const queryConfig = dashboardQueryConfig(project.id);
      let dashboardId = null;
      let guardDashboardId = null;
      let dashboardDeleted = false;
      let guardDashboardDeleted = false;
      let widgetId = null;

      const dashboard = await client.post(apiPath("/tracer/dashboard/"), {
        name: dashboardName,
        description: "Temporary dashboard for widget PUT coverage.",
      });
      dashboardId = dashboard?.id;
      assert(isUuid(dashboardId), "Dashboard create did not return a UUID id.");
      cleanup.defer("delete OBS-API-019 dashboard", () => {
        if (dashboardDeleted || !dashboardId) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
          ),
        );
      });

      const guardDashboard = await client.post(apiPath("/tracer/dashboard/"), {
        name: `${dashboardName} guard`,
        description: "Temporary dashboard for wrong-parent PUT guard.",
      });
      guardDashboardId = guardDashboard?.id;
      assert(
        isUuid(guardDashboardId),
        "Guard dashboard create did not return a UUID id.",
      );
      cleanup.defer("delete OBS-API-019 guard dashboard", () => {
        if (guardDashboardDeleted || !guardDashboardId) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/tracer/dashboard/{id}/", { id: guardDashboardId }),
          ),
        );
      });

      const widget = await client.post(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/", {
          dashboard_pk: dashboardId,
        }),
        {
          name: "PUT coverage widget",
          description: "Initial widget state",
          position: 0,
          width: 6,
          height: 4,
          query_config: queryConfig,
          chart_config: { chart_type: "line" },
        },
      );
      widgetId = widget?.id;
      assertWidgetPayload(widget, {
        widgetId,
        name: "PUT coverage widget",
        width: 6,
        chartType: "line",
      });

      const putPayload = {
        name: "PUT coverage widget updated",
        description: "Full replacement through PUT",
        position: 3,
        width: 7,
        height: 6,
        query_config: queryConfig,
        chart_config: { chart_type: "bar", show_legend: false },
      };
      const putWidget = await client.put(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
          dashboard_pk: dashboardId,
          id: widgetId,
        }),
        putPayload,
      );
      assertWidgetPayload(putWidget, {
        widgetId,
        name: putPayload.name,
        width: putPayload.width,
        chartType: putPayload.chart_config.chart_type,
      });
      assert(
        putWidget.description === putPayload.description &&
          putWidget.position === putPayload.position &&
          putWidget.height === putPayload.height,
        "Widget PUT did not replace description, position, and height.",
      );
      assert(
        asArray(putWidget.query_config?.metrics).length === 1 &&
          putWidget.query_config.metrics[0].name === "latency",
        "Widget PUT did not preserve the dashboard query metric config.",
      );

      const wrongDashboardError = await expectApiJourneyHttpStatus(
        404,
        () =>
          client.put(
            apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
              dashboard_pk: guardDashboardId,
              id: widgetId,
            }),
            { ...putPayload, name: "Wrong dashboard mutation should fail" },
          ),
        "Dashboard widget PUT under wrong dashboard",
      );
      const afterGuard = await client.get(
        apiPath("/tracer/dashboard/{dashboard_pk}/widgets/{id}/", {
          dashboard_pk: dashboardId,
          id: widgetId,
        }),
      );
      assert(
        afterGuard.name === putPayload.name,
        "Wrong-dashboard widget PUT mutated the original widget.",
      );

      const dashboardDetail = await client.get(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
      const detailWidget = asArray(dashboardDetail.widgets).find(
        (row) => row.id === widgetId,
      );
      assert(
        detailWidget?.name === putPayload.name &&
          detailWidget?.width === putPayload.width &&
          detailWidget?.chart_config?.chart_type ===
            putPayload.chart_config.chart_type,
        "Dashboard detail did not reload the PUT-updated widget.",
      );

      const activeAudit = await loadDashboardDbAudit({
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [widgetId],
      });
      assertDashboardDbAudit(activeAudit, {
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [widgetId],
        expectedDashboardDeleted: false,
        expectedActiveWidgetCount: 1,
      });
      const widgetAudit = asArray(activeAudit.widgets).find(
        (row) => row.id === widgetId,
      );
      assert(
        widgetAudit?.name === putPayload.name &&
          widgetAudit?.position === putPayload.position &&
          widgetAudit?.width === putPayload.width &&
          widgetAudit?.height === putPayload.height &&
          widgetAudit?.metric_count === 1 &&
          widgetAudit?.chart_type === putPayload.chart_config.chart_type,
        "Dashboard widget DB audit did not match the PUT-updated row.",
      );

      await client.delete(
        apiPath("/tracer/dashboard/{id}/", { id: guardDashboardId }),
      );
      guardDashboardDeleted = true;
      await client.delete(
        apiPath("/tracer/dashboard/{id}/", { id: dashboardId }),
      );
      dashboardDeleted = true;

      const deletedAudit = await loadDashboardDbAudit({
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [widgetId],
      });
      assertDashboardDbAudit(deletedAudit, {
        organizationId,
        workspaceId,
        dashboardId,
        widgetIds: [widgetId],
        expectedDashboardDeleted: true,
        expectedActiveWidgetCount: 0,
      });

      evidence.push({
        project_id: project.id,
        dashboard_id: dashboardId,
        guard_dashboard_id: guardDashboardId,
        widget_id: widgetId,
        widget_put_name: putPayload.name,
        widget_put_width: putPayload.width,
        wrong_dashboard_put_status: wrongDashboardError.status,
        active_widget_count_after_delete: deletedAudit.active_widget_count,
      });
    },
  },
  {
    id: "OBS-API-017",
    title: "Charts fetch graph and generated CRUD route guards",
    tags: ["observe", "charts", "safe", "dead-code-audit"],
    async run({ client, evidence }) {
      const project = await resolveObserveProject(client, evidence);
      const chartGraph = await client.get(
        apiPath("/tracer/charts/fetch_graph/"),
        {
          query: {
            project_id: project.id,
            interval: "day",
            property: "average",
            req_data_config: { id: "latency", type: "SYSTEM_METRIC" },
            filters: [],
          },
        },
      );
      assert(
        chartGraph?.metric_name === "latency" ||
          Array.isArray(chartGraph?.data),
        "Charts fetch_graph did not return the single system metric shape.",
      );

      const chartId = "00000000-0000-4000-8000-000000000017";
      const payload = {
        project_id: project.id,
        interval: "day",
        property: "average",
        req_data_config: { id: "latency", type: "SYSTEM_METRIC" },
      };
      const guardCalls = [
        ["GET", () => client.get(apiPath("/tracer/charts/"))],
        ["POST", () => client.post(apiPath("/tracer/charts/"), payload)],
        [
          "GET detail",
          () => client.get(apiPath("/tracer/charts/{id}/", { id: chartId })),
        ],
        [
          "PUT",
          () =>
            client.put(
              apiPath("/tracer/charts/{id}/", { id: chartId }),
              payload,
            ),
        ],
        [
          "PATCH",
          () =>
            client.patch(apiPath("/tracer/charts/{id}/", { id: chartId }), {
              property: "p95",
            }),
        ],
        [
          "DELETE",
          () => client.delete(apiPath("/tracer/charts/{id}/", { id: chartId })),
        ],
      ];
      const guardedMethods = [];
      for (const [method, fn] of guardCalls) {
        const error = await expectApiJourneyHttpStatus(
          405,
          fn,
          `Charts generated ${method} route`,
        );
        assert(
          String(error.body?.detail || "").includes("fetch_graph"),
          `Charts generated ${method} route did not point callers to fetch_graph.`,
        );
        guardedMethods.push(method);
      }

      evidence.push({
        project_id: project.id,
        chart_metric_name: chartGraph?.metric_name || "latency",
        guarded_methods: guardedMethods,
      });
    },
  },
  {
    id: "OBS-API-018",
    title:
      "Observe trace annotation bulk write, values readback, update, and CRUD guards",
    tags: ["observe", "annotations", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      user,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const userId = currentUserId(user);
      assert(
        isUuid(userId),
        "Authenticated user id did not resolve for trace annotation audit.",
      );

      const { project, span, detail } = await resolveObserveSpanForLifecycle(
        client,
        cleanup,
        runId,
        evidence,
      );
      const suffix = journeySafeId(runId);
      const spanId = span.span_id || span.id;
      const traceId = span.trace_id || detail.trace;
      assert(
        String(spanId || "").trim(),
        "Trace annotation span seed did not include span id.",
      );
      assert(
        isUuid(traceId),
        "Trace annotation span seed did not include trace id.",
      );

      const labelName = `OBS-API-018 trace quality ${suffix}`;
      const createdLabel = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "star",
          description:
            "Temporary trace annotation label for API journey coverage.",
          project: project.id,
          settings: { no_of_stars: 5 },
          allow_notes: true,
        },
      );
      const label = createdLabel?.id
        ? createdLabel
        : await findAnnotationLabelByName(client, labelName);
      const labelId = label?.id;
      assert(
        isUuid(labelId),
        "Trace annotation label create did not yield an id.",
      );
      cleanup.defer("delete OBS-API-018 temporary label", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
          ),
        ),
      );

      const routeId = "00000000-0000-4000-8000-000000000018";
      const guardCalls = [
        ["GET", () => client.get(apiPath("/tracer/trace-annotation/"))],
        ["POST", () => client.post(apiPath("/tracer/trace-annotation/"), {})],
        [
          "GET detail",
          () =>
            client.get(
              apiPath("/tracer/trace-annotation/{id}/", { id: routeId }),
            ),
        ],
        [
          "PUT",
          () =>
            client.put(
              apiPath("/tracer/trace-annotation/{id}/", { id: routeId }),
              {},
            ),
        ],
        [
          "PATCH",
          () =>
            client.patch(
              apiPath("/tracer/trace-annotation/{id}/", { id: routeId }),
              {},
            ),
        ],
        [
          "DELETE",
          () =>
            client.delete(
              apiPath("/tracer/trace-annotation/{id}/", { id: routeId }),
            ),
        ],
      ];
      const guardedMethods = [];
      for (const [method, fn] of guardCalls) {
        const error = await expectApiJourneyHttpStatus(
          405,
          fn,
          `TraceAnnotation generated ${method} route`,
        );
        const detailText = String(error.body?.detail || "");
        assert(
          detailText.includes("bulk-annotation") &&
            detailText.includes("get_annotation_values"),
          `TraceAnnotation generated ${method} route did not point callers to supported routes.`,
        );
        guardedMethods.push(method);
      }

      const noteText = `OBS-API-018 trace note ${suffix}`;
      const createResult = await client.post(
        apiPath("/tracer/bulk-annotation/"),
        {
          records: [
            {
              observation_span_id: spanId,
              annotations: [{ annotation_label_id: labelId, value_float: 4 }],
              notes: [{ text: noteText }],
            },
          ],
        },
      );
      assert(
        createResult?.annotations_created === 1 &&
          createResult?.notes_created === 1 &&
          createResult?.errors_count === 0,
        `Bulk trace annotation create returned unexpected counters: ${JSON.stringify(
          createResult,
        )}`,
      );

      const spanValues = await client.get(
        apiPath("/tracer/trace-annotation/get_annotation_values/"),
        {
          query: {
            observation_span_id: spanId,
            annotators: JSON.stringify([userId]),
          },
        },
      );
      const createdAnnotation = asArray(spanValues?.annotations).find(
        (annotation) => annotation.annotation_label_id === labelId,
      );
      assert(
        createdAnnotation?.id,
        "Trace annotation values did not include created score.",
      );
      assert(
        Number(createdAnnotation.annotation_value) === 4,
        "Trace annotation values did not return the created star rating.",
      );
      assert(
        asArray(spanValues?.notes).some((note) => note.notes === noteText),
        "Trace annotation values did not return the created span note.",
      );

      cleanup.defer("hard delete OBS-API-018 annotation artifacts", () =>
        hardDeleteTraceAnnotationArtifacts({
          scoreIds: [createdAnnotation.id],
          spanId,
          traceId,
          labelId,
          userId,
          noteText,
        }),
      );

      const traceValues = await client.get(
        apiPath("/tracer/trace-annotation/get_annotation_values/"),
        {
          query: {
            trace_id: traceId,
            annotators: JSON.stringify([userId]),
          },
        },
      );
      assert(
        asArray(traceValues?.annotations).some(
          (annotation) =>
            annotation.id === createdAnnotation.id &&
            Number(annotation.annotation_value) === 4,
        ),
        "Trace-level annotation values did not include the root span annotation.",
      );

      const updateResult = await client.post(
        apiPath("/tracer/bulk-annotation/"),
        {
          records: [
            {
              observation_span_id: spanId,
              annotations: [{ annotation_label_id: labelId, value_float: 5 }],
            },
          ],
        },
      );
      assert(
        updateResult?.annotations_created === 0 &&
          updateResult?.annotations_updated === 1 &&
          updateResult?.warnings_count === 1 &&
          updateResult?.errors_count === 0,
        `Bulk trace annotation update returned unexpected counters: ${JSON.stringify(
          updateResult,
        )}`,
      );

      const updatedValues = await client.get(
        apiPath("/tracer/trace-annotation/get_annotation_values/"),
        {
          query: {
            observation_span_id: spanId,
            annotators: JSON.stringify([userId]),
          },
        },
      );
      const updatedAnnotation = asArray(updatedValues?.annotations).find(
        (annotation) => annotation.id === createdAnnotation.id,
      );
      assert(
        Number(updatedAnnotation?.annotation_value) === 5,
        "Trace annotation duplicate write did not update the existing score value.",
      );

      const audit = await loadTraceAnnotationDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        spanId,
        traceId,
        labelId,
        userId,
        noteText,
      });
      assert(
        Number(audit.active_score_count) === 1,
        "Trace annotation audit found wrong score count.",
      );
      assert(
        asArray(audit.score_workspace_ids).every((id) => id === workspaceId),
        "Trace annotation score did not retain the active workspace id.",
      );
      assert(
        asArray(audit.score_values).some(
          (value) => Number(value?.rating) === 5,
        ),
        "Trace annotation DB audit did not see the updated score value.",
      );
      assert(
        Number(audit.active_note_count) === 1,
        "Trace annotation audit found wrong note count.",
      );
      assert(
        Number(audit.legacy_trace_annotation_count) === 0,
        "Bulk trace annotation should not create legacy trace_annotation rows.",
      );

      evidence.push({
        project_id: project.id,
        span_id: spanId,
        trace_id: traceId,
        label_id: labelId,
        score_id: createdAnnotation.id,
        guarded_methods: guardedMethods,
        annotation_value_after_update: Number(
          updatedAnnotation.annotation_value,
        ),
        active_score_count: audit.active_score_count,
        active_note_count: audit.active_note_count,
        legacy_trace_annotation_count: audit.legacy_trace_annotation_count,
      });
    },
  },
  {
    id: "OBS-API-013",
    title: "Observe span list, detail, loading, export, index, root, and tags",
    tags: ["observe", "spans", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const { project, span, detail, observeList } =
        await resolveObserveSpanForLifecycle(client, cleanup, runId, evidence);
      const spanId = span.span_id || span.id;
      const traceId = span.trace_id || detail.trace;
      const projectVersionId = detail.project_version;
      assert(
        String(spanId || "").trim(),
        "Selected observe span did not include span_id.",
      );
      assert(
        isUuid(traceId),
        "Selected observe span did not include a UUID trace_id.",
      );
      assert(
        isUuid(projectVersionId),
        "Selected observe span did not include a project_version id required by loading/base span APIs.",
      );

      const observeRows = asArray(observeList);
      assert(
        observeRows.some((row) => row.span_id === spanId),
        "Observe span list did not include the selected span.",
      );
      assert(
        responseCount(observeList) >= observeRows.length,
        "Observe span list metadata count was smaller than returned table rows.",
      );

      const detailPayload = await client.get(
        apiPath("/tracer/observation-span/{id}/", { id: spanId }),
      );
      const detailSpan = observationSpanPayload(detailPayload);
      assertObservationSpanDetail(detailSpan, {
        spanId,
        traceId,
        projectId: project.id,
        projectVersionId,
      });

      const loadingPayload = await client.get(
        apiPath("/tracer/observation-span/retrieve_loading/"),
        { query: { observation_span_id: spanId } },
      );
      const loadingSpan = observationSpanPayload(loadingPayload);
      assertObservationSpanDetail(loadingSpan, {
        spanId,
        traceId,
        projectId: project.id,
        projectVersionId,
      });
      assert(
        loadingPayload?.evals_metrics &&
          typeof loadingPayload.evals_metrics === "object",
        "retrieve_loading did not include evals_metrics object.",
      );

      const rootSpans = await client.get(
        apiPath("/tracer/observation-span/root-spans/"),
        { query: { trace_ids: [traceId] } },
      );
      const rootSpanId = rootSpans?.[traceId];
      assert(
        String(rootSpanId || "").trim(),
        "root-spans did not return a root span for the selected trace.",
      );

      const observeIndex = await client.get(
        queryWithFilters(
          apiPath(
            "/tracer/observation-span/get_trace_id_by_index_spans_as_observe/",
          ),
          EMPTY_FILTERS,
          {
            project_id: project.id,
            span_id: spanId,
          },
        ),
      );
      assertSpanIndexPayload(observeIndex, "Observe span index");

      const baseSpanList = await client.get(
        queryWithFilters(
          apiPath("/tracer/observation-span/list_spans/"),
          EMPTY_FILTERS,
          {
            project_version_id: projectVersionId,
            page_number: 0,
            page_size: 5,
          },
        ),
      );
      assert(
        asArray(baseSpanList).some((row) => row.span_id === spanId),
        "Base span list did not include the selected project-version span.",
      );

      const baseIndex = await client.get(
        queryWithFilters(
          apiPath(
            "/tracer/observation-span/get_trace_id_by_index_spans_as_base/",
          ),
          EMPTY_FILTERS,
          {
            project_version_id: projectVersionId,
            span_id: spanId,
          },
        ),
      );
      assertSpanIndexPayload(baseIndex, "Base span index");

      const exportFilters = canonicalTextFilter("span_id", "equals", spanId);
      const csv = await client.get(
        queryWithFilters(
          apiPath("/tracer/observation-span/get_spans_export_data/"),
          exportFilters,
          { project_id: project.id },
        ),
      );
      assert(
        typeof csv === "string" &&
          csv.includes("span_id") &&
          csv.includes("trace_id"),
        "Span export did not return CSV headers for span_id and trace_id.",
      );

      const originalTags = normalizeTags(detailSpan.tags);
      const temporaryTag = `api-journey-span-${runId}`;
      cleanup.defer("restore OBS-API-013 span tags", () =>
        client.post(
          apiPath("/tracer/observation-span/update-tags/"),
          { span_id: spanId, tags: originalTags },
          { okStatuses: [200, 400, 404] },
        ),
      );

      const updatedTags = uniqueTags([...originalTags, temporaryTag]);
      const updated = await client.post(
        apiPath("/tracer/observation-span/update-tags/"),
        { span_id: spanId, tags: updatedTags },
      );
      assert(
        normalizeTags(updated.tags).includes(temporaryTag),
        "Span update-tags did not return the temporary tag.",
      );

      const updatedDetail = observationSpanPayload(
        await client.get(
          apiPath("/tracer/observation-span/{id}/", { id: spanId }),
        ),
      );
      assert(
        normalizeTags(updatedDetail.tags).includes(temporaryTag),
        "Span detail did not read back the temporary tag.",
      );

      const audit = await loadObservationSpanDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        spanId,
        traceId,
      });
      assertObservationSpanDbAudit(audit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        spanId,
        traceId,
        rootSpanId,
        expectedTag: temporaryTag,
      });

      const restored = await client.post(
        apiPath("/tracer/observation-span/update-tags/"),
        { span_id: spanId, tags: originalTags },
      );
      assert(
        arraysEqual(normalizeTags(restored.tags), originalTags),
        "Span update-tags revert did not restore original tags.",
      );

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        project_version_id: projectVersionId,
        span_id: spanId,
        trace_id: traceId,
        root_span_id: rootSpanId,
        observe_span_total_rows: observeList?.metadata?.total_rows ?? null,
        base_span_total_rows: baseSpanList?.metadata?.total_rows ?? null,
        export_bytes: csv.length,
        temporary_tag: temporaryTag,
        db_visible_span_count: audit.visible_span_count,
      });
    },
  },
  {
    id: "OBS-API-014",
    title: "Observe span raw list, update, bulk, OTEL, and label delete",
    tags: ["observe", "spans", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const { project, span, detail } = await resolveObserveSpanForLifecycle(
        client,
        cleanup,
        runId,
        evidence,
      );
      const suffix = journeySafeId(runId);
      const spanId = span.span_id || span.id;
      const traceId = span.trace_id || detail.trace;
      const projectVersionId = detail.project_version;
      assert(
        String(spanId || "").trim(),
        "Selected observe span did not include span_id.",
      );
      assert(
        isUuid(traceId),
        "Selected observe span did not include a UUID trace_id.",
      );
      assert(
        isUuid(projectVersionId),
        "Selected observe span did not include a project_version id required by write APIs.",
      );

      const rawList = await client.get(apiPath("/tracer/observation-span/"), {
        query: {
          project_id: project.id,
          trace_id: traceId,
          page_number: 0,
          page_size: 25,
        },
      });
      assert(
        asArray(rawList).some((row) => row.id === spanId),
        "Raw observation-span viewset list did not include the disposable span.",
      );

      const patchedName = `api journey span patch ${suffix}`;
      const patched = await client.patch(
        apiPath("/tracer/observation-span/{id}/", { id: spanId }),
        {
          name: patchedName,
          status_message: `patched by api journey ${suffix}`,
          metadata: { source: "api-journey", run_id: runId, patched: true },
        },
      );
      assert(
        patched?.name === patchedName,
        "Observation span PATCH did not echo updated name.",
      );

      const patchDetail = observationSpanPayload(
        await client.get(
          apiPath("/tracer/observation-span/{id}/", { id: spanId }),
        ),
      );
      assert(
        patchDetail.name === patchedName,
        "Observation span PATCH did not persist name.",
      );
      assert(
        patchDetail.metadata?.patched === true,
        "Observation span PATCH did not persist metadata.",
      );

      const putName = `api journey span put ${suffix}`;
      const putPayload = observationSpanWritePayload({
        projectId: project.id,
        projectVersionId,
        traceId,
        name: putName,
        runId,
        metadata: { source: "api-journey", run_id: runId, put: true },
        statusMessage: `put by api journey ${suffix}`,
      });
      const put = await client.put(
        apiPath("/tracer/observation-span/{id}/", { id: spanId }),
        putPayload,
      );
      assert(
        put?.name === putName,
        "Observation span PUT did not echo replacement name.",
      );

      const putDetail = observationSpanPayload(
        await client.get(
          apiPath("/tracer/observation-span/{id}/", { id: spanId }),
        ),
      );
      assertObservationSpanDetail(putDetail, {
        spanId,
        traceId,
        projectId: project.id,
        projectVersionId,
      });
      assert(
        putDetail.name === putName,
        "Observation span PUT did not persist name.",
      );
      assert(
        putDetail.metadata?.put === true,
        "Observation span PUT did not persist replacement metadata.",
      );

      const explicitBulkSpanId = `api_journey_bulk_span_${suffix}`;
      const bulkStart = new Date(Date.now() - 1500).toISOString();
      const bulkEnd = new Date().toISOString();
      const bulk = await client.post(
        apiPath("/tracer/observation-span/bulk_create/"),
        {
          observation_spans: [
            observationSpanWritePayload({
              id: explicitBulkSpanId,
              projectId: project.id,
              projectVersionId,
              traceId,
              name: `api journey explicit bulk span ${suffix}`,
              runId,
              startTime: bulkStart,
              endTime: bulkEnd,
              metadata: {
                source: "api-journey",
                run_id: runId,
                bulk: "explicit",
              },
            }),
            observationSpanWritePayload({
              projectId: project.id,
              projectVersionId,
              traceId,
              name: `api journey generated bulk span ${suffix}`,
              runId,
              startTime: bulkStart,
              endTime: bulkEnd,
              metadata: {
                source: "api-journey",
                run_id: runId,
                bulk: "generated",
              },
            }),
          ],
        },
      );
      const bulkIds = asArray(bulk?.["Observation Span IDs"]);
      assert(
        bulkIds.includes(explicitBulkSpanId),
        "Observation span bulk_create did not return the explicit span id.",
      );
      const generatedBulkSpanId = bulkIds.find(
        (id) => id && id !== explicitBulkSpanId,
      );
      assert(
        String(generatedBulkSpanId || "").trim(),
        "Observation span bulk_create did not generate an id for the id-less span.",
      );
      for (const bulkSpanId of [explicitBulkSpanId, generatedBulkSpanId]) {
        cleanup.defer(`delete OBS-API-014 bulk span ${bulkSpanId}`, () =>
          client.delete(
            apiPath("/tracer/observation-span/{id}/", { id: bulkSpanId }),
            {
              okStatuses: [200, 204, 400, 404],
            },
          ),
        );
      }

      const bulkList = await client.get(apiPath("/tracer/observation-span/"), {
        query: {
          project_id: project.id,
          trace_id: traceId,
          page_number: 0,
          page_size: 50,
        },
      });
      const bulkRows = asArray(bulkList);
      for (const bulkSpanId of [explicitBulkSpanId, generatedBulkSpanId]) {
        assert(
          bulkRows.some((row) => row.id === bulkSpanId),
          `Raw observation-span list did not include bulk span ${bulkSpanId}.`,
        );
      }

      const otelTraceId = randomUUID();
      const otelSpanId = `api_journey_otel_span_${suffix}`;
      cleanup.defer("delete OBS-API-014 OTEL trace", () =>
        client.delete(apiPath("/tracer/trace/{id}/", { id: otelTraceId }), {
          okStatuses: [200, 204, 400, 404],
        }),
      );
      cleanup.defer("delete OBS-API-014 OTEL span", () =>
        client.delete(
          apiPath("/tracer/observation-span/{id}/", { id: otelSpanId }),
          {
            okStatuses: [200, 204, 400, 404],
          },
        ),
      );

      const nowNs = Date.now() * 1_000_000;
      const otel = await client.post(
        apiPath("/tracer/observation-span/create_otel_span/"),
        [
          {
            project_name: project.name,
            project_type: "observe",
            trace_id: otelTraceId,
            span_id: otelSpanId,
            name: `api journey otel span ${suffix}`,
            start_time: nowNs - 2_000_000,
            end_time: nowNs,
            latency: 2,
            status: "OK",
            attributes: {
              "gen_ai.span.kind": "llm",
              "gen_ai.request.model": "api-journey-model",
              "gen_ai.usage.input_tokens": 1,
              "gen_ai.usage.output_tokens": 1,
              "input.value": JSON.stringify({ prompt: "otel input" }),
              "output.value": JSON.stringify({ response: "otel output" }),
              "tag.tags": ["api-journey"],
            },
          },
        ],
      );
      assert(
        asArray(otel?.ids).includes(otelSpanId),
        "Observation span create_otel_span did not return the requested span id.",
      );
      const otelDetail = observationSpanPayload(
        await client.get(
          apiPath("/tracer/observation-span/{id}/", { id: otelSpanId }),
        ),
      );
      assert(
        otelDetail.id === otelSpanId,
        "OTEL span detail returned wrong id.",
      );
      assert(
        otelDetail.trace === otelTraceId,
        "OTEL span detail returned wrong trace id.",
      );
      const otelProjectId = otelDetail.project;
      if (otelDetail.project !== project.id) {
        const resolutionAudit = await loadOtelProjectResolutionAudit({
          sourceProjectId: project.id,
          resolvedProjectId: otelProjectId,
          organizationId,
          workspaceId,
        });
        assert(
          resolutionAudit.same_name === true &&
            resolutionAudit.source_workspace_id === null &&
            resolutionAudit.resolved_workspace_id === workspaceId,
          `OTEL span resolved to unexpected project. Audit: ${JSON.stringify(
            resolutionAudit,
          )}`,
        );
        evidence.push({
          endpoint: "observe otel active-workspace project resolution",
          source_project_id: project.id,
          resolved_project_id: otelProjectId,
          project_name: resolutionAudit.source_project_name,
        });
      }

      const labelName = `OBS-API-014 temporary label ${suffix}`;
      const createdLabel = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description:
            "Temporary label for observation-span delete label coverage.",
          project: project.id,
          settings: {
            placeholder: "OBS-API-014",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const label = createdLabel?.id
        ? createdLabel
        : await findAnnotationLabelByName(client, labelName);
      const labelId = label?.id;
      assert(
        isUuid(labelId),
        "Temporary annotation label create did not yield an id.",
      );
      cleanup.defer("delete OBS-API-014 temporary label", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
          ),
        ),
      );

      const deleteLabel = await client.delete(
        apiPath("/tracer/observation-span/delete_annotation_label/"),
        { query: { label_id: labelId } },
      );
      assert(
        String(deleteLabel?.message || "").includes("deleted"),
        "Observation span delete_annotation_label did not confirm deletion.",
      );

      const audit = await loadObservationSpanMutationDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        spanIds: [spanId, explicitBulkSpanId, generatedBulkSpanId, otelSpanId],
        labelId,
      });
      assertObservationSpanMutationDbAudit(audit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        spanIds: [spanId, explicitBulkSpanId, generatedBulkSpanId, otelSpanId],
        updatedSpanId: spanId,
        updatedSpanName: putName,
        expectedProjectIdsBySpanId: {
          [otelSpanId]: otelProjectId,
        },
        labelId,
      });

      evidence.push({
        project_id: project.id,
        project_name: project.name || null,
        project_version_id: projectVersionId,
        span_id: spanId,
        trace_id: traceId,
        bulk_span_ids: [explicitBulkSpanId, generatedBulkSpanId],
        otel_trace_id: otelTraceId,
        otel_span_id: otelSpanId,
        label_id: labelId,
        audited_span_count: asArray(audit.spans).length,
        label_deleted: audit.label?.deleted === true,
      });
    },
  },
  {
    id: "OBS-API-015",
    title:
      "Observe trace raw CRUD, bulk, list, export, compare, navigation, agent graph, and delete cascade",
    tags: ["observe", "traces", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const suffix = journeySafeId(runId);
      const projectName = `api journey trace ${suffix}`;
      const createdProject = await client.post(apiPath("/tracer/project/"), {
        name: projectName,
        model_type: "GenerativeLLM",
        trace_type: "observe",
        metadata: { source: "api-journey", run_id: runId },
      });
      const project = { id: createdProject.project_id, name: projectName };
      assert(
        isUuid(project.id),
        "Trace lifecycle project create returned no id.",
      );

      const projectVersion = await client.post(
        apiPath("/tracer/project-version/"),
        {
          project: project.id,
          name: `api journey trace run ${suffix}`,
          metadata: { source: "api-journey", run_id: runId },
        },
      );
      const projectVersionId =
        projectVersion.project_version_id || projectVersion.id;
      assert(
        isUuid(projectVersionId),
        "Trace lifecycle project-version create returned no id.",
      );

      const traceA = await client.post(
        apiPath("/tracer/trace/"),
        traceWritePayload({
          projectId: project.id,
          projectVersionId,
          name: `api journey trace alpha ${suffix}`,
          runId,
          marker: "alpha",
        }),
      );
      const traceAId = traceA.id || traceA.trace_id || traceA.trace?.id;
      assert(isUuid(traceAId), "Trace create returned no id.");

      const traceBId = randomUUID();
      const bulk = await client.post(apiPath("/tracer/trace/bulk_create/"), {
        traces: [
          traceWritePayload({
            id: traceBId,
            projectId: project.id,
            projectVersionId,
            name: `api journey trace beta ${suffix}`,
            runId,
            marker: "beta",
          }),
        ],
      });
      assert(
        asArray(bulk?.["Trace IDs"]).map(String).includes(traceBId),
        "Trace bulk_create did not return the requested trace id.",
      );

      const spanAId = `api_journey_trace_alpha_span_${suffix}`;
      const spanBId = `api_journey_trace_beta_span_${suffix}`;
      const spanAChildId = `api_journey_trace_alpha_child_span_${suffix}`;
      const spanAName = `api journey trace alpha root ${suffix}`;
      const spanAChildName = `api journey trace alpha tool ${suffix}`;
      const spanStart = new Date(Date.now() - 2000).toISOString();
      const spanEnd = new Date().toISOString();
      for (const [spanId, traceId, marker, spanName] of [
        [spanAId, traceAId, "alpha", spanAName],
        [spanBId, traceBId, "beta", `api journey trace beta root ${suffix}`],
      ]) {
        const span = await client.post(
          apiPath("/tracer/observation-span/"),
          observationSpanWritePayload({
            id: spanId,
            projectId: project.id,
            projectVersionId,
            traceId,
            name: spanName,
            runId,
            startTime: spanStart,
            endTime: spanEnd,
            metadata: {
              source: "api-journey",
              run_id: runId,
              trace_marker: marker,
            },
          }),
        );
        assert(
          span?.id === spanId,
          `Trace lifecycle span ${marker} returned wrong id.`,
        );
      }
      const childSpan = await client.post(
        apiPath("/tracer/observation-span/"),
        observationSpanWritePayload({
          id: spanAChildId,
          projectId: project.id,
          projectVersionId,
          traceId: traceAId,
          parentSpanId: spanAId,
          observationType: "tool",
          name: spanAChildName,
          runId,
          startTime: spanStart,
          endTime: spanEnd,
          metadata: {
            source: "api-journey",
            run_id: runId,
            trace_marker: "alpha-child",
          },
        }),
      );
      assert(
        childSpan?.id === spanAChildId,
        "Trace lifecycle child span returned wrong id.",
      );

      cleanup.defer("hard delete OBS-API-015 artifacts", () =>
        hardDeleteTraceLifecycleArtifacts({
          projectId: project.id,
          projectVersionId,
          traceIds: [traceAId, traceBId],
          spanIds: [spanAId, spanBId, spanAChildId],
        }),
      );

      const rawList = await client.get(apiPath("/tracer/trace/"), {
        query: {
          project_id: project.id,
          project_version_id: projectVersionId,
          trace_ids: [traceAId, traceBId].join(","),
        },
      });
      const rawRows = asArray(rawList);
      assert(
        rawRows.some((row) => row.id === traceAId) &&
          rawRows.some((row) => row.id === traceBId),
        "Raw trace list did not include both disposable traces.",
      );

      const detail = await client.get(
        apiPath("/tracer/trace/{id}/", { id: traceAId }),
      );
      assert(
        detail?.trace?.id === traceAId,
        "Trace detail returned wrong trace id.",
      );
      assert(
        asArray(detail?.observation_spans).length > 0,
        "Trace detail did not include the created observation span tree.",
      );

      const patched = await client.patch(
        apiPath("/tracer/trace/{id}/", { id: traceAId }),
        {
          name: `api journey trace alpha patched ${suffix}`,
          metadata: { source: "api-journey", run_id: runId, patched: true },
        },
      );
      assert(
        patched?.metadata?.patched === true,
        "Trace PATCH did not echo metadata update.",
      );

      const putPayload = traceWritePayload({
        projectId: project.id,
        projectVersionId,
        name: `api journey trace alpha put ${suffix}`,
        runId,
        marker: "alpha-put",
        metadata: { source: "api-journey", run_id: runId, put: true },
      });
      const put = await client.put(
        apiPath("/tracer/trace/{id}/", { id: traceAId }),
        putPayload,
      );
      assert(
        put?.name === putPayload.name,
        "Trace PUT did not echo replacement name.",
      );

      const tag = `api-journey-trace-${suffix}`;
      const tagUpdate = await client.patch(
        apiPath("/tracer/trace/{id}/tags/", { id: traceAId }),
        {
          tags: ["api-journey", tag],
        },
      );
      assert(
        asArray(tagUpdate?.tags).includes(tag),
        "Trace tag update did not return the temporary tag.",
      );

      const listed = await client.get(
        queryWithFilters(apiPath("/tracer/trace/list_traces/"), EMPTY_FILTERS, {
          project_version_id: projectVersionId,
          page_number: 0,
          page_size: 10,
        }),
      );
      const listedRows = asArray(listed);
      assert(
        listedRows.some((row) => row.trace_id === traceAId) &&
          listedRows.some((row) => row.trace_id === traceBId),
        "Trace list_traces did not include both disposable traces.",
      );

      const compare = await client.post(
        apiPath("/tracer/trace/compare_traces/"),
        {
          project_version_ids: [projectVersionId],
          index: 0,
        },
      );
      assert(
        Number(compare?.total_traces || 0) >= 1,
        "Trace compare_traces did not return disposable comparison rows.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(
          compare?.trace_comparison || {},
          projectVersionId,
        ),
        "Trace compare_traces omitted the requested project version key.",
      );

      const baseIndex = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/get_trace_id_by_index/"),
          EMPTY_FILTERS,
          {
            project_version_id: projectVersionId,
            trace_id: traceAId,
          },
        ),
      );
      assertSpanIndexPayload(baseIndex, "Base trace index");

      const observeIndex = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/get_trace_id_by_index_observe/"),
          EMPTY_FILTERS,
          {
            project_id: project.id,
            trace_id: traceAId,
          },
        ),
      );
      assertSpanIndexPayload(observeIndex, "Observe trace index");

      const evalNames = await client.get(
        apiPath("/tracer/trace/get_eval_names/"),
        {
          query: { project_id: project.id },
        },
      );
      assert(
        Array.isArray(asArray(evalNames)),
        "Trace get_eval_names did not return a list.",
      );

      const graphProperties = await client.get(
        apiPath("/tracer/trace/get_properties/"),
      );
      assert(
        asArray(graphProperties).includes("Average") &&
          asArray(graphProperties).includes("P95"),
        "Trace get_properties did not return the graph property catalog.",
      );

      const agentGraph = await client.get(
        queryWithFilters(apiPath("/tracer/trace/agent_graph/"), EMPTY_FILTERS, {
          project_id: project.id,
        }),
      );
      const agentNodes = asArray(agentGraph?.nodes);
      const agentEdges = asArray(agentGraph?.edges);
      assert(
        agentNodes.some((node) => node.id === `llm:${spanAName}`) &&
          agentNodes.some((node) => node.id === `tool:${spanAChildName}`),
        "Trace agent_graph did not include the disposable root and child nodes.",
      );
      assert(
        agentEdges.some(
          (edge) =>
            edge.source === `llm:${spanAName}` &&
            edge.target === `tool:${spanAChildName}` &&
            Number(edge.transition_count) >= 1,
        ),
        "Trace agent_graph did not include the disposable parent-child edge.",
      );

      const csv = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/get_trace_export_data/"),
          EMPTY_FILTERS,
          { project_id: project.id },
        ),
      );
      assert(
        typeof csv === "string" &&
          csv.includes("trace_id") &&
          csv.includes(traceAId),
        "Trace export did not return CSV headers and the disposable trace id.",
      );

      const activeAudit = await loadTraceLifecycleDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        traceIds: [traceAId, traceBId],
        spanIds: [spanAId, spanBId, spanAChildId],
      });
      assertTraceLifecycleDbAudit(activeAudit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        activeTraceIds: [traceAId, traceBId],
        deletedTraceIds: [],
        activeSpanIds: [spanAId, spanBId, spanAChildId],
        deletedSpanIds: [],
        expectedTag: tag,
      });

      await client.delete(apiPath("/tracer/trace/{id}/", { id: traceAId }));
      let deletedDetailRejected = false;
      try {
        await client.get(apiPath("/tracer/trace/{id}/", { id: traceAId }));
      } catch (error) {
        if (
          error instanceof ApiJourneyError &&
          (error.status === 400 ||
            error.status === 404 ||
            error.body?.status === false)
        ) {
          deletedDetailRejected = true;
        } else {
          throw error;
        }
      }
      assert(
        deletedDetailRejected,
        "Trace detail still loaded after generic DELETE.",
      );

      const afterDeleteAudit = await loadTraceLifecycleDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        traceIds: [traceAId, traceBId],
        spanIds: [spanAId, spanBId, spanAChildId],
      });
      assertTraceLifecycleDbAudit(afterDeleteAudit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        activeTraceIds: [traceBId],
        deletedTraceIds: [traceAId],
        activeSpanIds: [spanBId],
        deletedSpanIds: [spanAId, spanAChildId],
        expectedTag: tag,
      });

      await client.delete(apiPath("/tracer/trace/{id}/", { id: traceBId }));
      await client.delete(
        apiPath("/tracer/project-version/{id}/", { id: projectVersionId }),
        {
          okStatuses: [200, 204, 400, 404],
        },
      );
      const cleanupAudit = await hardDeleteTraceLifecycleArtifacts({
        projectId: project.id,
        projectVersionId,
        traceIds: [traceAId, traceBId],
        spanIds: [spanAId, spanBId, spanAChildId],
      });
      assert(
        Number(cleanupAudit.remaining_trace_count) === 0 &&
          Number(cleanupAudit.remaining_span_count) === 0 &&
          Number(cleanupAudit.remaining_project_version_count) === 0 &&
          Number(cleanupAudit.remaining_project_count) === 0,
        `Trace lifecycle cleanup left rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        project_id: project.id,
        project_version_id: projectVersionId,
        trace_ids: [traceAId, traceBId],
        span_ids: [spanAId, spanBId, spanAChildId],
        raw_list_count: rawRows.length,
        list_traces_count: listedRows.length,
        compare_total_traces: compare.total_traces,
        export_bytes: csv.length,
        agent_graph_nodes: agentNodes.length,
        agent_graph_edges: agentEdges.length,
        generic_delete_cascaded: true,
        cleanup_trace_count: cleanupAudit.remaining_trace_count,
      });
    },
  },
  {
    id: "OBS-API-016",
    title:
      "Observe trace-session raw CRUD, list, detail, export, eval logs, and delete cascade",
    tags: ["observe", "sessions", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const suffix = journeySafeId(runId);
      const projectName = `api journey session ${suffix}`;
      const createdProject = await client.post(apiPath("/tracer/project/"), {
        name: projectName,
        model_type: "GenerativeLLM",
        trace_type: "observe",
        metadata: { source: "api-journey", run_id: runId },
      });
      const project = { id: createdProject.project_id, name: projectName };
      assert(
        isUuid(project.id),
        "Trace-session lifecycle project create returned no id.",
      );

      const projectVersion = await client.post(
        apiPath("/tracer/project-version/"),
        {
          project: project.id,
          name: `api journey session run ${suffix}`,
          metadata: { source: "api-journey", run_id: runId },
        },
      );
      const projectVersionId =
        projectVersion.project_version_id || projectVersion.id;
      assert(
        isUuid(projectVersionId),
        "Trace-session project-version create returned no id.",
      );

      const createdSession = await client.post(
        apiPath("/tracer/trace-session/"),
        traceSessionWritePayload({
          projectId: project.id,
          name: `api journey session ${suffix}`,
          bookmarked: false,
        }),
      );
      const sessionId = createdSession.id || createdSession.trace_session_id;
      assert(isUuid(sessionId), "Trace-session create returned no id.");

      const trace = await client.post(
        apiPath("/tracer/trace/"),
        traceWritePayload({
          projectId: project.id,
          projectVersionId,
          sessionId,
          name: `api journey session trace ${suffix}`,
          runId,
          marker: "session",
        }),
      );
      const traceId = trace.id || trace.trace_id || trace.trace?.id;
      assert(isUuid(traceId), "Trace-session trace create returned no id.");

      const spanId = `api_journey_session_span_${suffix}`;
      const spanStart = new Date(Date.now() - 2500).toISOString();
      const spanEnd = new Date().toISOString();
      const span = await client.post(
        apiPath("/tracer/observation-span/"),
        observationSpanWritePayload({
          id: spanId,
          projectId: project.id,
          projectVersionId,
          traceId,
          name: `api journey session root ${suffix}`,
          runId,
          startTime: spanStart,
          endTime: spanEnd,
          metadata: {
            source: "api-journey",
            run_id: runId,
            session_id: sessionId,
          },
        }),
      );
      assert(
        span?.id === spanId,
        "Trace-session span create returned wrong id.",
      );

      const sessionEvalLog = await insertTraceSessionEvalLog({
        sessionId,
        explanation: `api journey session eval ${suffix}`,
      });
      const evalLogId = sessionEvalLog.eval_log_id;
      assert(isUuid(evalLogId), "Trace-session eval log seed returned no id.");

      cleanup.defer("hard delete OBS-API-016 artifacts", () =>
        hardDeleteTraceSessionLifecycleArtifacts({
          projectId: project.id,
          projectVersionId,
          sessionId,
          traceIds: [traceId],
          spanIds: [spanId],
          evalLogIds: [evalLogId],
        }),
      );

      const rawList = await client.get(apiPath("/tracer/trace-session/"), {
        query: { project_id: project.id },
      });
      const rawRows = asArray(rawList);
      assert(
        rawRows.some((row) => row.id === sessionId),
        "Raw trace-session list did not include the disposable session.",
      );

      const detail = await client.get(
        apiPath("/tracer/trace-session/{id}/", { id: sessionId }),
      );
      assert(
        detail?.session_metadata?.session_id === sessionId,
        "Trace-session detail returned wrong session id.",
      );
      assert(
        asArray(detail?.response).some((row) => row.trace_id === traceId),
        "Trace-session detail did not include the disposable trace.",
      );

      const patched = await client.patch(
        apiPath("/tracer/trace-session/{id}/", { id: sessionId }),
        {
          name: `api journey session patched ${suffix}`,
          bookmarked: true,
        },
      );
      assert(
        patched?.bookmarked === true,
        "Trace-session PATCH did not persist bookmark.",
      );

      const putPayload = traceSessionWritePayload({
        projectId: project.id,
        name: `api journey session put ${suffix}`,
        bookmarked: false,
      });
      const put = await client.put(
        apiPath("/tracer/trace-session/{id}/", { id: sessionId }),
        putPayload,
      );
      assert(
        put?.name === putPayload.name,
        "Trace-session PUT did not echo replacement name.",
      );
      assert(
        put?.bookmarked === false,
        "Trace-session PUT did not persist bookmark replacement.",
      );

      const listSessions = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace-session/list_sessions/"),
          EMPTY_FILTERS,
          {
            project_id: project.id,
            page_number: 0,
            page_size: 10,
          },
        ),
      );
      const sessionRows = asArray(listSessions?.table);
      assert(
        sessionRows.some((row) => row.session_id === sessionId),
        "Trace-session list_sessions did not include the disposable session.",
      );

      const csv = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace-session/get_trace_session_export_data/"),
          EMPTY_FILTERS,
          { project_id: project.id },
        ),
      );
      assert(
        typeof csv === "string" &&
          csv.includes("session_id") &&
          csv.includes(sessionId),
        "Trace-session export did not return CSV headers and the disposable session id.",
      );

      const evalLogs = await client.get(
        apiPath("/tracer/trace-session/{id}/eval_logs/", { id: sessionId }),
        {
          query: { page: 0, page_size: 5 },
        },
      );
      assert(
        Number(evalLogs?.total || 0) >= 1,
        "Trace-session eval_logs returned no rows.",
      );
      assert(
        asArray(evalLogs?.items).some(
          (item) => item.id === evalLogId && item.session_id === sessionId,
        ),
        "Trace-session eval_logs did not include the seeded session eval.",
      );

      const activeAudit = await loadTraceSessionLifecycleDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        sessionId,
        traceIds: [traceId],
        spanIds: [spanId],
        evalLogIds: [evalLogId],
      });
      assertTraceSessionLifecycleDbAudit(activeAudit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        sessionId,
        activeTraceIds: [traceId],
        deletedTraceIds: [],
        activeSpanIds: [spanId],
        deletedSpanIds: [],
        activeEvalLogIds: [evalLogId],
        deletedEvalLogIds: [],
      });

      await client.delete(
        apiPath("/tracer/trace-session/{id}/", { id: sessionId }),
      );
      let deletedDetailRejected = false;
      try {
        await client.get(
          apiPath("/tracer/trace-session/{id}/", { id: sessionId }),
        );
      } catch (error) {
        if (
          error instanceof ApiJourneyError &&
          (error.status === 400 ||
            error.status === 404 ||
            error.body?.status === false)
        ) {
          deletedDetailRejected = true;
        } else {
          throw error;
        }
      }
      assert(
        deletedDetailRejected,
        "Trace-session detail still loaded after generic DELETE.",
      );

      const afterDeleteAudit = await loadTraceSessionLifecycleDbAudit({
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        sessionId,
        traceIds: [traceId],
        spanIds: [spanId],
        evalLogIds: [evalLogId],
      });
      assertTraceSessionLifecycleDbAudit(afterDeleteAudit, {
        organizationId,
        workspaceId,
        projectId: project.id,
        projectVersionId,
        sessionId,
        activeTraceIds: [],
        deletedTraceIds: [traceId],
        activeSpanIds: [],
        deletedSpanIds: [spanId],
        activeEvalLogIds: [],
        deletedEvalLogIds: [evalLogId],
      });

      await client.delete(
        apiPath("/tracer/project-version/{id}/", { id: projectVersionId }),
        {
          okStatuses: [200, 204, 400, 404],
        },
      );
      const cleanupAudit = await hardDeleteTraceSessionLifecycleArtifacts({
        projectId: project.id,
        projectVersionId,
        sessionId,
        traceIds: [traceId],
        spanIds: [spanId],
        evalLogIds: [evalLogId],
      });
      assert(
        Number(cleanupAudit.remaining_session_count) === 0 &&
          Number(cleanupAudit.remaining_trace_count) === 0 &&
          Number(cleanupAudit.remaining_span_count) === 0 &&
          Number(cleanupAudit.remaining_eval_log_count) === 0 &&
          Number(cleanupAudit.remaining_project_version_count) === 0 &&
          Number(cleanupAudit.remaining_project_count) === 0,
        `Trace-session cleanup left rows behind: ${JSON.stringify(cleanupAudit)}`,
      );

      evidence.push({
        project_id: project.id,
        project_version_id: projectVersionId,
        session_id: sessionId,
        trace_id: traceId,
        span_id: spanId,
        eval_log_id: evalLogId,
        raw_list_count: rawRows.length,
        list_sessions_count: sessionRows.length,
        export_bytes: csv.length,
        generic_delete_cascaded: true,
        cleanup_session_count: cleanupAudit.remaining_session_count,
      });
    },
  },
  {
    id: "ERR-API-001",
    title: "Error Feed list filters, detail tabs, root cause, and access scope",
    tags: ["error-feed", "safe", "filters", "security"],
    async run({ client, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const list = await client.get(apiPath("/tracer/feed/issues/"), {
        query: {
          limit: 5,
          offset: 0,
          sort_by: "last_seen",
          sort_dir: "desc",
        },
      });
      const rows = asArray(list);
      if (!rows.length) {
        skip("No Error Feed rows are available for feed coverage.");
      }
      assertErrorFeedListPayload(list, rows);

      const audit = await loadErrorFeedDbAudit({ organizationId, workspaceId });
      assert(
        Number(list.total) === Number(audit.visible_error_group_count),
        "Error Feed total did not match workspace-scoped DB audit.",
      );

      const row = rows[0];
      const clusterId = row.cluster_id;
      assert(
        String(clusterId || "").trim(),
        "Error Feed row omitted cluster_id.",
      );

      const filteredChecks = [
        {
          name: "project",
          query: { project_id: row.project_id },
          predicate: (candidate) => candidate.project_id === row.project_id,
        },
        {
          name: "status",
          query: { status: row.status },
          predicate: (candidate) => candidate.status === row.status,
        },
        {
          name: "severity",
          query: { severity: row.severity },
          predicate: (candidate) => candidate.severity === row.severity,
        },
        {
          name: "source",
          query: { source: row.source },
          predicate: (candidate) => candidate.source === row.source,
        },
      ];
      if (row.fix_layer) {
        filteredChecks.push({
          name: "fix_layer",
          query: { fix_layer: titleCaseFixLayer(row.fix_layer) },
          predicate: (candidate) => candidate.fix_layer === row.fix_layer,
        });
      }
      for (const check of filteredChecks) {
        const filtered = await client.get(apiPath("/tracer/feed/issues/"), {
          query: { ...check.query, limit: 25, offset: 0 },
        });
        const filteredRows = asArray(filtered);
        assert(
          filteredRows.some((candidate) => candidate.cluster_id === clusterId),
          `Error Feed ${check.name} filter did not include selected cluster.`,
        );
        assert(
          filteredRows.every(check.predicate),
          `Error Feed ${check.name} filter returned an unrelated row.`,
        );
      }

      const searchTerm = String(row.error?.name || clusterId)
        .slice(0, 16)
        .trim();
      const searched = await client.get(apiPath("/tracer/feed/issues/"), {
        query: { search: searchTerm, limit: 25, offset: 0 },
      });
      assert(
        asArray(searched).some(
          (candidate) => candidate.cluster_id === clusterId,
        ),
        "Error Feed search did not include selected cluster.",
      );

      const severitySorted = await client.get(apiPath("/tracer/feed/issues/"), {
        query: { sort_by: "severity", sort_dir: "desc", limit: 25, offset: 0 },
      });
      assertSeveritySorted(asArray(severitySorted), "desc");

      const detail = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/", { cluster_id: clusterId }),
      );
      assert(
        detail?.row?.cluster_id === clusterId,
        "Error Feed detail did not return the selected cluster.",
      );

      const overview = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/overview/", {
          cluster_id: clusterId,
        }),
      );
      assert(
        Array.isArray(overview?.representative_traces),
        "Error Feed overview omitted representative_traces array.",
      );
      assert(
        Array.isArray(overview?.pattern_summary?.insights),
        "Error Feed overview omitted pattern summary insights.",
      );

      const traces = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/traces/", {
          cluster_id: clusterId,
        }),
        { query: { limit: 10, offset: 0 } },
      );
      assert(
        Number(traces?.aggregates?.total_traces ?? 0) >=
          asArray(traces?.traces).length,
        "Error Feed traces aggregate was smaller than returned trace rows.",
      );

      const trends = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/trends/", {
          cluster_id: clusterId,
        }),
        { query: { days: 30 } },
      );
      assert(
        asArray(trends?.metrics).length > 0,
        "Error Feed trends omitted metrics.",
      );
      assert(
        Array.isArray(trends?.activity_heatmap),
        "Error Feed trends omitted activity heatmap.",
      );

      const traceId =
        row.trace_id ||
        asArray(traces?.traces)[0]?.id ||
        overview.representative_traces?.[0]?.id;
      const sidebar = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/sidebar/", {
          cluster_id: clusterId,
        }),
        { query: traceId ? { trace_id: traceId } : {} },
      );
      assert(sidebar?.timeline, "Error Feed sidebar omitted timeline.");
      assert(sidebar?.ai_metadata, "Error Feed sidebar omitted AI metadata.");

      let rootCauseStatus = null;
      if (traceId) {
        const rootCause = await client.get(
          apiPath("/tracer/feed/issues/{cluster_id}/root-cause/", {
            cluster_id: clusterId,
          }),
          { query: { trace_id: traceId } },
        );
        rootCauseStatus = rootCause?.status || null;
        assert(
          ["idle", "running", "done", "failed"].includes(rootCauseStatus),
          "Error Feed root-cause endpoint returned an unknown status.",
        );

        const legacyAnalysis = await client.get(
          apiPath("/tracer/trace-error-analysis/{trace_id}/", {
            trace_id: traceId,
          }),
        );
        assert(
          legacyAnalysis && typeof legacyAnalysis === "object",
          "Legacy trace-error-analysis endpoint did not return an object.",
        );
      }

      let hiddenClusterStatus = null;
      if (audit.hidden_cluster_id) {
        try {
          await client.get(
            apiPath("/tracer/feed/issues/{cluster_id}/overview/", {
              cluster_id: audit.hidden_cluster_id,
            }),
          );
          throw new Error("Hidden Error Feed cluster overview was readable.");
        } catch (error) {
          assert(
            error?.status === 404,
            `Hidden Error Feed cluster should return 404, got ${error?.status || error.message}.`,
          );
          hiddenClusterStatus = error.status;
        }
      }

      evidence.push({
        cluster_id: clusterId,
        project_id: row.project_id,
        total: list.total,
        db_visible_error_group_count: audit.visible_error_group_count,
        severity: row.severity,
        severity_filter_count: responseCount(
          await client.get(apiPath("/tracer/feed/issues/"), {
            query: { severity: row.severity, limit: 200, offset: 0 },
          }),
        ),
        traces_total: traces.total,
        root_cause_status: rootCauseStatus,
        hidden_cluster_status: hiddenClusterStatus,
      });
    },
  },
  {
    id: "ERR-API-002",
    title: "Trace error analysis task config read/update and cleanup",
    tags: ["error-feed", "tasks", "mutating"],
    async run({ client, cleanup, organizationId, workspaceId, evidence }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const list = await client.get(apiPath("/tracer/feed/issues/"), {
        query: { limit: 5, offset: 0, sort_by: "last_seen", sort_dir: "desc" },
      });
      const row = asArray(list)[0];
      if (!row?.project_id) {
        skip(
          "No Error Feed project is available for trace-error-task coverage.",
        );
      }
      const projectId = row.project_id;
      const before = await loadTraceErrorTaskDbState({
        organizationId,
        workspaceId,
        projectId,
      });
      evidence.push({
        trace_task_project_id: projectId,
        trace_task_before: before,
      });
      assert(
        before.project_visible === true,
        "Selected Error Feed project is not DB-visible.",
      );

      cleanup.defer("restore trace error task config", () =>
        restoreTraceErrorTaskDbState({ projectId, before }),
      );

      const initial = await client.get(
        apiPath("/tracer/trace-error-task/{project_id}/", {
          project_id: projectId,
        }),
      );
      assert(
        initial?.project_id === projectId,
        "Trace error task GET project_id mismatch.",
      );
      assert(
        ["running", "waiting", "paused"].includes(initial.status),
        "Trace error task GET returned an invalid status.",
      );

      let invalidRateStatus = null;
      try {
        await client.post(
          apiPath("/tracer/trace-error-task/{project_id}/", {
            project_id: projectId,
          }),
          { sampling_rate: 1.5 },
        );
      } catch (error) {
        invalidRateStatus = error.status;
      }
      assert(
        invalidRateStatus === 400,
        "Invalid trace error task sampling_rate was accepted.",
      );

      const currentRate = Number(initial.sampling_rate);
      const nextRate = currentRate === 0.23 ? 0.17 : 0.23;
      const updated = await client.post(
        apiPath("/tracer/trace-error-task/{project_id}/", {
          project_id: projectId,
        }),
        { sampling_rate: nextRate, status: "paused" },
      );
      assert(
        updated?.project_id === projectId,
        "Trace error task POST project_id mismatch.",
      );
      assert(
        Number(updated?.new_rate) === nextRate,
        "Trace error task POST new_rate mismatch.",
      );
      assert(
        updated?.status === "paused",
        "Trace error task POST did not persist paused status.",
      );

      const readback = await client.get(
        apiPath("/tracer/trace-error-task/{project_id}/", {
          project_id: projectId,
        }),
      );
      assert(
        Number(readback?.sampling_rate) === nextRate,
        "Trace error task readback rate mismatch.",
      );
      assert(
        readback?.status === "paused",
        "Trace error task readback status mismatch.",
      );

      const after = await loadTraceErrorTaskDbState({
        organizationId,
        workspaceId,
        projectId,
      });
      assert(
        after.task_exists === true,
        "Trace error task DB row was not persisted.",
      );
      assert(
        Number(after.sampling_rate) === nextRate,
        "Trace error task DB rate mismatch.",
      );
      assert(after.status === "paused", "Trace error task DB status mismatch.");

      const cleanupAudit = await restoreTraceErrorTaskDbState({
        projectId,
        before,
      });
      const expectedActiveTaskCount = before.task_exists ? 1 : 0;
      evidence.push({
        trace_task_cleanup_expected_active_count: expectedActiveTaskCount,
        trace_task_cleanup_audit: cleanupAudit,
      });
      assert(
        Number(cleanupAudit.touched_rows) > 0 &&
          Number(cleanupAudit.active_task_count) === expectedActiveTaskCount,
        "Trace error task cleanup did not restore the original state.",
      );

      evidence.push({
        project_id: projectId,
        cluster_id: row.cluster_id,
        task_row_existed_before: before.task_row_exists,
        active_task_existed_before: before.task_exists,
        get_created: initial.created,
        original_sampling_rate: before.sampling_rate,
        updated_sampling_rate: nextRate,
        updated_status: updated.status,
        invalid_rate_status: invalidRateStatus,
        cleanup_active_task_count: cleanupAudit.active_task_count,
      });
    },
  },
  {
    id: "ERR-API-003",
    title: "Error Feed issue mutation, cached deep analysis, and Linear guard",
    tags: ["error-feed", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      user,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const userInfo = await client.get(apiPath("/accounts/user-info/"));
      const email = currentUserEmail(userInfo) || currentUserEmail(user);
      assert(
        email.includes("@"),
        "Authenticated user-info did not include an email.",
      );

      const list = await client.get(apiPath("/tracer/feed/issues/"), {
        query: { limit: 25, offset: 0, sort_by: "last_seen", sort_dir: "desc" },
      });
      const row =
        asArray(list).find((item) => item?.trace_id) || asArray(list)[0];
      if (!row?.cluster_id || !row?.project_id || !row?.trace_id) {
        skip(
          "No Error Feed row with a linked trace is available for mutation coverage.",
        );
      }
      const clusterId = row.cluster_id;
      const projectId = row.project_id;
      const traceId = row.trace_id;

      const before = await loadErrorFeedMutationDbAudit({
        organizationId,
        workspaceId,
        clusterId,
        traceId,
      });
      assert(
        before.cluster_visible === true,
        "Selected Error Feed cluster is not DB-visible.",
      );
      assert(
        before.trace_visible === true,
        "Selected Error Feed trace is not DB-visible.",
      );

      let restored = false;
      cleanup.defer("restore ERR-API-003 feed issue", () => {
        if (restored) return null;
        return restoreErrorFeedMutationDbState({ before });
      });

      const nextStatus =
        before.status === "acknowledged" ? "for_review" : "acknowledged";
      const nextSeverity = before.priority === "low" ? "high" : "low";
      const patched = await client.patch(
        apiPath("/tracer/feed/issues/{cluster_id}/", { cluster_id: clusterId }),
        {
          status: nextStatus,
          severity: nextSeverity,
          assignee: email,
        },
      );
      assert(
        patched?.row?.status === nextStatus,
        "Error Feed PATCH did not persist status.",
      );
      assert(
        patched?.row?.severity === nextSeverity,
        "Error Feed PATCH did not persist severity.",
      );
      assert(
        asArray(patched?.row?.assignees).some(
          (assignee) => String(assignee).toLowerCase() === email.toLowerCase(),
        ),
        "Error Feed PATCH did not assign the current user.",
      );
      const assignedAudit = await loadErrorFeedMutationDbAudit({
        organizationId,
        workspaceId,
        clusterId,
        traceId,
      });
      assert(
        assignedAudit.status === nextStatus &&
          assignedAudit.priority === nextSeverity &&
          String(assignedAudit.assignee_email).toLowerCase() ===
            email.toLowerCase(),
        "Error Feed PATCH DB audit did not match assigned status/severity/user.",
      );

      await client.patch(
        apiPath("/tracer/feed/issues/{cluster_id}/", { cluster_id: clusterId }),
        { assignee: null },
      );
      const clearedAudit = await loadErrorFeedMutationDbAudit({
        organizationId,
        workspaceId,
        clusterId,
        traceId,
      });
      assert(
        !clearedAudit.assignee_id,
        "Error Feed PATCH assignee=null did not clear DB assignee.",
      );

      const analysisId = randomUUID();
      let cacheCleaned = false;
      await seedErrorFeedDeepAnalysisCache({
        analysisId,
        traceId,
        projectId,
      });
      cleanup.defer("delete ERR-API-003 deep-analysis cache", () => {
        if (cacheCleaned) return null;
        return deleteErrorFeedDeepAnalysisCache({ analysisId, before });
      });

      const rootCause = await client.get(
        apiPath("/tracer/feed/issues/{cluster_id}/root-cause/", {
          cluster_id: clusterId,
        }),
        { query: { trace_id: traceId } },
      );
      assert(
        rootCause?.status === "done",
        "Seeded deep analysis cache did not read as done.",
      );
      const dispatch = await client.post(
        apiPath("/tracer/feed/issues/{cluster_id}/deep-analysis/", {
          cluster_id: clusterId,
        }),
        { trace_id: traceId, force: false },
      );
      assert(
        dispatch?.status === "done" && dispatch?.trace_id === traceId,
        "Cached deep-analysis POST did not return done without re-dispatch.",
      );

      const linearTeams = await client.get(
        apiPath("/tracer/feed/integrations/linear/teams/"),
      );
      let linearCreateStatus = null;
      if (linearTeams?.connected === false) {
        const linearError = await expectApiJourneyHttpStatus(
          400,
          () =>
            client.post(
              apiPath("/tracer/feed/issues/{cluster_id}/create-linear-issue/", {
                cluster_id: clusterId,
              }),
              { team_id: `api-journey-${journeySafeId(runId)}` },
            ),
          "Error Feed create Linear issue without integration",
        );
        linearCreateStatus = linearError.status;
        const afterLinear = await loadErrorFeedMutationDbAudit({
          organizationId,
          workspaceId,
          clusterId,
          traceId,
        });
        assert(
          !afterLinear.external_issue_url && !afterLinear.external_issue_id,
          "Linear guard mutated external issue fields without an integration.",
        );
      }

      const cacheCleanup = await deleteErrorFeedDeepAnalysisCache({
        analysisId,
        before,
      });
      cacheCleaned = true;
      const restoreAudit = await restoreErrorFeedMutationDbState({ before });
      restored = true;

      evidence.push({
        cluster_id: clusterId,
        project_id: projectId,
        trace_id: traceId,
        patched_status: nextStatus,
        patched_severity: nextSeverity,
        assigned_user: email,
        assignee_cleared: !clearedAudit.assignee_id,
        deep_analysis_dispatch_status: dispatch.status,
        linear_connected: Boolean(linearTeams?.connected),
        linear_create_status: linearCreateStatus,
        restored_status: restoreAudit.status,
        restored_priority: restoreAudit.priority,
        cache_cleanup_deleted: cacheCleanup.deleted_analysis_count,
      });
    },
  },
  {
    id: "TSK-API-001",
    title: "Observe task list, search, project scope, detail, logs, and usage",
    tags: ["observe", "tasks", "safe", "data-roundtrip"],
    async run({ client, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const list = await client.get(
        apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
        {
          query: {
            page_number: 0,
            page_size: 10,
            sort_params: JSON.stringify([
              { column_id: "created_at", direction: "desc" },
            ]),
          },
        },
      );
      const rows = asArray(list.table || list);
      if (!rows.length) {
        skip("No Observe tasks are available for task list coverage.");
      }
      assert(
        Number(list?.metadata?.total_rows) >= rows.length,
        "Task list metadata total_rows was inconsistent.",
      );

      const task =
        rows.find((row) => row?.id && row?.filters_applied?.project_id) ||
        rows[0];
      const taskProjectId = task.project_id || task.filters_applied?.project_id;
      assert(isUuid(task.id), "Task list row omitted a valid id.");
      assert(isUuid(taskProjectId), "Task list row omitted project_id.");
      assert(String(task.name || "").trim(), "Task list row omitted name.");
      assert(
        String(task.project_name || "").trim(),
        "Task list row omitted project_name.",
      );
      assert(
        asArray(task.evals_applied).length > 0,
        "Task list row omitted evals_applied.",
      );

      const audit = await loadEvalTaskDbAudit({
        organizationId,
        workspaceId,
        taskId: task.id,
      });
      assert(audit.task_visible === true, "Selected task was not DB-visible.");
      assert(
        Number(audit.visible_task_with_evals_count) ===
          Number(list.metadata.total_rows),
        "Task list total did not match visible workspace-scoped DB task count.",
      );

      const searched = await client.get(
        apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
        {
          query: {
            page_number: 0,
            page_size: 10,
            name: task.name,
          },
        },
      );
      assert(
        asArray(searched.table || searched).some((row) => row.id === task.id),
        "Task name search did not return the selected task.",
      );

      const projectScoped = await client.get(
        apiPath("/tracer/eval-task/list_eval_tasks/"),
        {
          query: {
            page_number: 0,
            page_size: 10,
            project_id: taskProjectId,
          },
        },
      );
      assert(
        asArray(projectScoped.table || projectScoped).some(
          (row) => row.id === task.id,
        ),
        "Project-scoped task list did not return the selected task.",
      );

      const detail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: task.id },
        },
      );
      assert(detail?.id === task.id, "Task detail id mismatch.");
      assert(
        detail?.project_id === taskProjectId,
        "Task detail project mismatch.",
      );
      assert(
        asArray(detail.evals_applied).length ===
          Number(audit.selected_eval_count),
        "Task detail eval count did not match DB.",
      );

      const logs = await client.get(
        apiPath("/tracer/eval-task/get_eval_task_logs/"),
        {
          query: { eval_task_id: task.id },
        },
      );
      assert(
        Number(logs.errors_count) + Number(logs.success_count) ===
          Number(logs.total_count),
        "Task logs counts did not add up.",
      );
      assert(
        Number(logs.total_count) === Number(audit.selected_log_count),
        "Task logs total did not match DB.",
      );

      const usage = await client.get(apiPath("/tracer/eval-task/get_usage/"), {
        query: {
          eval_task_id: task.id,
          page: 1,
          page_size: 5,
          period: "30d",
        },
      });
      assert(
        usage?.eval_task_id === task.id,
        "Task usage eval_task_id mismatch.",
      );
      assert(
        Number(usage?.stats?.total_runs) === Number(audit.selected_log_count),
        "Task usage total_runs did not match DB.",
      );
      assert(Array.isArray(usage.chart), "Task usage omitted chart array.");
      assert(
        Array.isArray(usage?.logs?.results),
        "Task usage omitted log results.",
      );

      evidence.push({
        task_id: task.id,
        task_name: task.name,
        project_id: taskProjectId,
        project_name: task.project_name,
        list_total: list.metadata.total_rows,
        db_org_task_with_evals_count: audit.org_task_with_evals_count,
        db_workspace_task_with_evals_count:
          audit.workspace_task_with_evals_count,
        db_null_workspace_task_with_evals_count:
          audit.null_workspace_task_with_evals_count,
        db_other_workspace_task_with_evals_count:
          audit.other_workspace_task_with_evals_count,
        db_visible_task_with_evals_count: audit.visible_task_with_evals_count,
        selected_eval_count: audit.selected_eval_count,
        selected_log_count: audit.selected_log_count,
        usage_period_used: usage.period_used,
        usage_log_rows: usage.logs.results.length,
      });
    },
  },
  {
    id: "TSK-API-002",
    title:
      "Observe task create, rename, update, pause, resume, and delete lifecycle",
    tags: ["observe", "tasks", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const list = await client.get(
        apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
        {
          query: {
            page_number: 0,
            page_size: 10,
            sort_params: JSON.stringify([
              { column_id: "created_at", direction: "desc" },
            ]),
          },
        },
      );
      const baseRows = asArray(list.table || list);
      if (!baseRows.length) {
        skip("No Observe task exists to provide a project/eval config seed.");
      }

      const seed =
        baseRows.find((row) => row?.id && row?.filters_applied?.project_id) ||
        baseRows[0];
      const seedProjectId = seed.project_id || seed.filters_applied?.project_id;
      assert(isUuid(seed.id), "Seed task row omitted id.");
      assert(isUuid(seedProjectId), "Seed task row omitted project_id.");

      const seedDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: seed.id },
        },
      );
      const seedEval = asArray(seedDetail.evals_applied)[0];
      if (!seedEval?.id) {
        skip("Seed Observe task has no eval config id for create coverage.");
      }

      const foreignConfig = await loadForeignEvalConfigCandidate({
        organizationId,
        workspaceId,
        projectId: seedProjectId,
      });
      if (foreignConfig?.config_id) {
        await expectApiJourneyHttpStatus(
          400,
          () =>
            client.post(apiPath("/tracer/eval-task/"), {
              project: seedProjectId,
              name: `api journey rejected task ${runId}`,
              run_type: "continuous",
              sampling_rate: 100,
              row_type: seedDetail.row_type || "spans",
              filters: { project_id: seedProjectId },
              evals: [foreignConfig.config_id],
            }),
          "Foreign eval config create guard",
        );
      }

      const taskName = `api journey task ${runId}`;
      const created = await client.post(apiPath("/tracer/eval-task/"), {
        project: seedProjectId,
        name: taskName,
        run_type: "continuous",
        sampling_rate: 100,
        row_type: seedDetail.row_type || "spans",
        filters: { project_id: seedProjectId },
        evals: [seedEval.id],
      });
      assert(isUuid(created?.id), "Task create did not return an id.");
      const createdTaskId = created.id;

      cleanup.defer("hard cleanup TSK-API-002 task artifacts", () =>
        deleteEvalTaskDbArtifacts({ taskId: createdTaskId }),
      );

      const createdDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: createdTaskId },
        },
      );
      assert(
        createdDetail.name === taskName,
        "Created task detail name mismatch.",
      );
      assert(
        createdDetail.project_id === seedProjectId,
        "Created task project mismatch.",
      );
      assert(
        asArray(createdDetail.evals_applied).some(
          (evalItem) => evalItem.id === seedEval.id,
        ),
        "Created task detail did not include the selected eval config.",
      );

      await expectApiJourneyHttpStatus(
        400,
        () =>
          client.post(
            apiPath("/tracer/eval-task/pause_eval_task/"),
            {},
            { query: { eval_task_id: createdTaskId } },
          ),
        "Pending task pause guard",
      );

      const renamedName = `${taskName} renamed`;
      const renamed = await client.patch(
        apiPath("/tracer/eval-task/{id}/", { id: createdTaskId }),
        { name: renamedName },
      );
      assert(
        renamed?.name === renamedName,
        "Detail PATCH rename did not persist.",
      );

      const updatedName = `${taskName} updated`;
      const updated = await client.patch(
        apiPath("/tracer/eval-task/update_eval_task/"),
        {
          eval_task_id: createdTaskId,
          name: updatedName,
          sampling_rate: 99,
          evals: [seedEval.id],
          edit_type: "edit_rerun",
        },
      );
      assert(
        updated?.task_id === createdTaskId,
        "Task update response id mismatch.",
      );

      const updatedDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: createdTaskId },
        },
      );
      assert(
        updatedDetail.name === updatedName,
        "Task update name did not persist.",
      );
      assert(
        Number(updatedDetail.sampling_rate) === 99,
        "Task update sampling rate did not persist.",
      );

      await setEvalTaskStatus({ taskId: createdTaskId, status: "running" });
      const paused = await client.post(
        apiPath("/tracer/eval-task/pause_eval_task/"),
        {},
        { query: { eval_task_id: createdTaskId } },
      );
      assert(paused?.message, "Running task pause did not return success.");
      const pausedDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: createdTaskId },
        },
      );
      assert(
        pausedDetail.status === "paused",
        "Pause did not persist paused status.",
      );

      const resumed = await client.post(
        apiPath("/tracer/eval-task/unpause_eval_task/"),
        {},
        { query: { eval_task_id: createdTaskId } },
      );
      assert(resumed?.message, "Paused task resume did not return success.");
      const resumedDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: createdTaskId },
        },
      );
      assert(
        resumedDetail.status === "pending",
        "Resume did not reset status to pending.",
      );

      const deleted = await client.post(
        apiPath("/tracer/eval-task/mark_eval_tasks_deleted/"),
        { eval_task_ids: [createdTaskId] },
      );
      assert(deleted?.message, "Task delete did not return success.");
      const cleanupAudit = await loadEvalTaskLifecycleAudit({
        taskId: createdTaskId,
      });
      assert(
        cleanupAudit.task_deleted === true,
        "Deleted task DB flag was not set.",
      );
      assert(
        cleanupAudit.task_status === "deleted",
        "Deleted task status mismatch.",
      );

      evidence.push({
        seed_task_id: seed.id,
        seed_project_id: seedProjectId,
        seed_eval_config_id: seedEval.id,
        foreign_eval_config_guard_checked: Boolean(foreignConfig?.config_id),
        created_task_id: createdTaskId,
        renamed_name: renamedName,
        updated_name: updatedName,
        final_public_deleted: cleanupAudit.task_deleted,
        task_logger_count: cleanupAudit.task_logger_count,
      });
    },
  },
  {
    id: "TSK-API-003",
    title: "Observe task linked trace source stores direct id filters",
    tags: ["observe", "tasks", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );

      const { seed, seedEval, projectId, trace, traceDetail } =
        await resolveEvalTaskSeedWithTrace(client, evidence);
      const traceId = trace.trace_id || trace.id;
      assert(isUuid(traceId), "Linked-source trace row omitted trace_id.");

      const taskName = `api journey linked trace task ${runId}`;
      const created = await client.post(apiPath("/tracer/eval-task/"), {
        project: projectId,
        name: taskName,
        run_type: "continuous",
        sampling_rate: 100,
        row_type: "traces",
        filters: {
          project_id: projectId,
          trace_id: [traceId],
        },
        evals: [seedEval.id],
      });
      assert(isUuid(created?.id), "Linked-source task create returned no id.");
      const createdTaskId = created.id;

      cleanup.defer("hard cleanup TSK-API-003 linked task artifacts", () =>
        deleteEvalTaskDbArtifacts({ taskId: createdTaskId }),
      );

      const taskDetail = await client.get(
        apiPath("/tracer/eval-task/get_eval_details/"),
        {
          query: { eval_id: createdTaskId },
        },
      );
      assert(
        taskDetail?.id === createdTaskId,
        "Linked task detail id mismatch.",
      );
      assert(
        taskDetail?.project_id === projectId,
        "Linked task project mismatch.",
      );
      assert(
        asArray(taskDetail?.filters_applied?.trace_id).includes(traceId),
        "Linked task detail did not preserve the trace_id filter.",
      );
      assert(
        asArray(taskDetail.evals_applied).some(
          (evalItem) => evalItem.id === seedEval.id,
        ),
        "Linked task detail did not include the selected eval config.",
      );

      const sourceDetail = await client.get(
        apiPath("/tracer/trace/{id}/", { id: traceId }),
      );
      const detailTraceId =
        sourceDetail?.trace_id || sourceDetail?.id || sourceDetail?.trace?.id;
      assert(
        detailTraceId === traceId,
        "Linked source trace detail did not return the selected trace.",
      );

      const filteredTraces = await client.get(
        queryWithFilters(
          apiPath("/tracer/trace/list_traces_of_session/"),
          canonicalTextFilter("trace_id", "equals", traceId),
          {
            project_id: projectId,
            page_number: 0,
            page_size: 5,
          },
        ),
      );
      assert(
        asArray(filteredTraces).some((row) => row.trace_id === traceId),
        "Trace list did not return the selected source under trace_id filter.",
      );

      const audit = await loadEvalTaskLinkedSourceAudit({
        organizationId,
        workspaceId,
        taskId: createdTaskId,
        projectId,
        traceId,
      });
      assert(
        audit.task_exists === true,
        "Linked task row was not found in DB.",
      );
      assert(
        audit.trace_id_filter_contains === true,
        "Linked task DB filters did not contain the selected trace_id.",
      );
      assert(
        audit.source_trace_exists === true,
        "Linked source trace was not visible in DB for the selected project.",
      );
      assert(
        Number(audit.matched_span_count) > 0,
        "Linked source trace has no matching spans for trace-row evaluation.",
      );

      const deleted = await client.post(
        apiPath("/tracer/eval-task/mark_eval_tasks_deleted/"),
        { eval_task_ids: [createdTaskId] },
      );
      assert(deleted?.message, "Linked task delete did not return success.");
      const cleanupAudit = await loadEvalTaskLifecycleAudit({
        taskId: createdTaskId,
      });
      assert(
        cleanupAudit.task_deleted === true,
        "Linked task public delete flag was not set.",
      );

      evidence.push({
        seed_task_id: seed.id,
        seed_project_id: projectId,
        seed_eval_config_id: seedEval.id,
        created_task_id: createdTaskId,
        source_trace_id: traceId,
        source_trace_name: trace.name || traceDetail?.name || null,
        source_url: `/dashboard/observe/${projectId}/trace/${traceId}`,
        filters_applied: taskDetail.filters_applied,
        matched_span_count: audit.matched_span_count,
        source_reverse_task_column_count:
          audit.source_reverse_task_column_count,
        public_deleted: cleanupAudit.task_deleted,
      });
    },
  },
  {
    id: "ALT-API-001",
    title: "Observe alert list, detail, metric options, graph, and logs",
    tags: ["observe", "alerts", "safe", "data-roundtrip", "db-audit"],
    async run({ client, organizationId, workspaceId, evidence }) {
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const project = await resolveObserveProject(client, evidence);

      const metricOptions = asArray(
        await client.get(apiPath("/tracer/user-alerts/metric-options/"), {
          query: { project_id: project.id },
        }),
      );
      assert(
        metricOptions.some((option) => option.id === "count_of_errors"),
        "Alert metric options omitted count_of_errors.",
      );

      const preview = await client.post(
        apiPath("/tracer/user-alerts/preview-graph/"),
        {
          project: project.id,
          metric_type: "count_of_errors",
          threshold_operator: "greater_than",
          threshold_type: "static",
          critical_threshold_value: 999999,
          warning_threshold_value: 999998,
          alert_frequency: 60,
          filters: {},
        },
      );
      assert(
        Array.isArray(preview) || typeof preview === "object",
        "Alert preview graph returned an invalid payload.",
      );

      const list = await client.get(
        apiPath("/tracer/user-alerts/list_monitors/"),
        {
          query: {
            page_number: 0,
            page_size: 10,
            sort_by: "created_at",
            sort_direction: "desc",
          },
        },
      );
      const rows = asArray(list.table || list);
      assert(
        Number(list?.metadata?.total_rows ?? rows.length) >= rows.length,
        "Alert list metadata total_rows was inconsistent.",
      );

      const audit = await loadAlertListDbAudit({ organizationId, workspaceId });
      assert(
        Number(audit.visible_monitor_count) ===
          Number(list.metadata.total_rows),
        "Alert list total did not match visible workspace-scoped DB monitor count.",
      );

      if (!rows.length) {
        skip("No Observe alerts are available for alert detail coverage.");
      }

      const alert = rows.find((row) => row?.id) || rows[0];
      assert(isUuid(alert.id), "Alert list row omitted a valid id.");
      assert(String(alert.name || "").trim(), "Alert list row omitted name.");
      assert(
        Object.prototype.hasOwnProperty.call(alert, "metric_type"),
        "Alert list row omitted API metric_type field.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(alert, "no_of_alerts"),
        "Alert list row omitted API no_of_alerts field.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(alert, "is_mute"),
        "Alert list row omitted API is_mute field.",
      );

      const detail = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: alert.id }),
        { query: { page_number: 0, page_size: 5 } },
      );
      assert(detail?.id === alert.id, "Alert detail id mismatch.");
      assert(
        detail?.workspace === workspaceId,
        "Alert detail workspace mismatch.",
      );
      assert(
        detail?.project === alert.project ||
          detail?.project === project.id ||
          isUuid(detail?.project),
        "Alert detail omitted project id.",
      );
      assert(detail?.logs?.metadata, "Alert detail omitted logs metadata.");

      const graph = await client.get(
        apiPath("/tracer/user-alerts/{id}/graph/", { id: alert.id }),
      );
      assert(
        Array.isArray(graph) || typeof graph === "object",
        "Alert graph returned an invalid payload.",
      );

      const logs = await client.get(
        apiPath("/tracer/user-alert-logs/{id}/list/", { id: alert.id }),
      );
      assert(Array.isArray(logs), "Alert log list did not return an array.");

      evidence.push({
        project_id: project.id,
        alert_id: alert.id,
        alert_name: alert.name,
        list_total: list.metadata.total_rows,
        db_visible_monitor_count: audit.visible_monitor_count,
        db_other_workspace_monitor_count: audit.other_workspace_monitor_count,
        metric_options_count: metricOptions.length,
        detail_log_total: detail.logs.metadata.total_rows,
        direct_log_rows: logs.length,
      });
    },
  },
  {
    id: "ALT-API-002",
    title:
      "Observe alert create, update, mute, duplicate, resolve, and delete lifecycle",
    tags: ["observe", "alerts", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const project = await resolveObserveProject(client, evidence);

      const alertName = `api journey alert ${runId}`;
      await client.post(apiPath("/tracer/user-alerts/"), {
        project: project.id,
        name: alertName,
        metric_type: "count_of_errors",
        threshold_operator: "greater_than",
        threshold_type: "static",
        critical_threshold_value: 999999,
        warning_threshold_value: 999998,
        alert_frequency: 60,
        filters: {},
      });

      const created = await findAlertByName(client, alertName);
      assert(
        created?.id,
        "Alert create did not produce a searchable list row.",
      );
      cleanup.defer("hard cleanup ALT-API-002 alert artifacts", () =>
        deleteAlertDbArtifacts({ alertIds: [created.id] }),
      );

      const createdAudit = await loadAlertLifecycleAudit({
        alertId: created.id,
      });
      assert(
        createdAudit.alert_exists === true,
        "Created alert DB row was not persisted.",
      );
      assert(
        createdAudit.workspace_id === workspaceId,
        "Created alert was not assigned to the request workspace.",
      );
      assert(
        createdAudit.project_id === project.id,
        "Created alert project mismatch.",
      );

      const createdDetail = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: created.id }),
      );
      assert(
        createdDetail.name === alertName,
        "Created alert detail name mismatch.",
      );
      assert(
        createdDetail.workspace === workspaceId,
        "Created alert detail workspace mismatch.",
      );

      const renamedName = `${alertName} renamed`;
      await client.patch(
        apiPath("/tracer/user-alerts/{id}/", { id: created.id }),
        {
          name: renamedName,
          critical_threshold_value: 999997,
          warning_threshold_value: 999996,
        },
      );
      const renamedDetail = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: created.id }),
      );
      assert(
        renamedDetail.name === renamedName,
        "Alert PATCH rename did not persist.",
      );
      assert(
        Number(renamedDetail.critical_threshold_value) === 999997,
        "Alert PATCH critical threshold did not persist.",
      );

      await client.post(apiPath("/tracer/user-alerts/bulk-mute/"), {
        ids: [created.id],
        is_mute: true,
      });
      let mutedAudit = await loadAlertLifecycleAudit({ alertId: created.id });
      assert(mutedAudit.is_mute === true, "Alert mute did not persist.");
      await client.post(apiPath("/tracer/user-alerts/bulk-mute/"), {
        ids: [created.id],
        is_mute: false,
      });
      mutedAudit = await loadAlertLifecycleAudit({ alertId: created.id });
      assert(mutedAudit.is_mute === false, "Alert unmute did not persist.");

      const copiedName = `${alertName} copy`;
      const copied = await client.post(
        apiPath("/tracer/user-alerts/duplicate/"),
        {
          id: created.id,
          name: copiedName,
        },
      );
      assert(isUuid(copied?.id), "Alert duplicate did not return a copied id.");
      cleanup.defer("hard cleanup ALT-API-002 copied alert artifacts", () =>
        deleteAlertDbArtifacts({ alertIds: [copied.id] }),
      );
      const copiedDetail = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: copied.id }),
      );
      assert(
        copiedDetail.name === copiedName,
        "Copied alert detail name mismatch.",
      );
      assert(
        copiedDetail.workspace === workspaceId,
        "Copied alert workspace mismatch.",
      );

      const logId = randomUUID();
      await insertAlertLog({
        alertId: created.id,
        logId,
        message: `api journey alert log ${runId}`,
      });
      const detailWithLog = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: created.id }),
        { query: { page_number: 0, page_size: 5 } },
      );
      assert(
        asArray(detailWithLog.logs?.results).some((log) => log.id === logId),
        "Alert detail logs did not include inserted log.",
      );
      await client.post(apiPath("/tracer/user-alert-logs/resolve/"), {
        log_ids: [logId],
      });
      const resolvedAudit = await loadAlertLifecycleAudit({
        alertId: created.id,
      });
      assert(
        resolvedAudit.unresolved_log_count === 0,
        "Alert log resolve did not persist.",
      );

      await client.delete(apiPath("/tracer/user-alerts/"), {
        body: { ids: [created.id, copied.id] },
      });
      const deletedAudit = await loadAlertLifecycleAudit({
        alertId: created.id,
      });
      const copiedDeletedAudit = await loadAlertLifecycleAudit({
        alertId: copied.id,
      });
      assert(
        deletedAudit.alert_deleted === true,
        "Alert delete did not set deleted flag.",
      );
      assert(
        copiedDeletedAudit.alert_deleted === true,
        "Copied alert delete did not set deleted flag.",
      );

      evidence.push({
        project_id: project.id,
        created_alert_id: created.id,
        copied_alert_id: copied.id,
        inserted_log_id: logId,
        renamed_name: renamedName,
        copied_name: copiedName,
        final_public_deleted: deletedAudit.alert_deleted,
        copied_public_deleted: copiedDeletedAudit.alert_deleted,
        resolved_log_count: resolvedAudit.resolved_log_count,
      });
    },
  },
  {
    id: "ALT-API-003",
    title: "Observe alert safe notification trigger creates resolvable logs",
    tags: ["observe", "alerts", "mutating", "notifications", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      assert(
        isUuid(organizationId),
        "Authenticated context did not resolve an organization id.",
      );
      assert(
        isUuid(workspaceId),
        "Authenticated context did not resolve a workspace id.",
      );
      const project = await resolveObserveProject(client, evidence);

      const alertName = `api journey safe notify ${runId}`;
      await client.post(apiPath("/tracer/user-alerts/"), {
        project: project.id,
        name: alertName,
        metric_type: "count_of_errors",
        threshold_operator: "greater_than",
        threshold_type: "static",
        critical_threshold_value: 0,
        alert_frequency: 60,
        filters: {},
      });

      const created = await findAlertByName(client, alertName);
      assert(
        created?.id,
        "Safe notification alert create did not produce a searchable row.",
      );
      cleanup.defer("hard cleanup ALT-API-003 alert artifacts", () =>
        deleteAlertDbArtifacts({ alertIds: [created.id] }),
      );

      const createdAudit = await loadAlertNotificationAudit({
        alertId: created.id,
      });
      assert(
        createdAudit.alert_exists === true,
        "Safe notification alert DB row was not persisted.",
      );
      assert(
        createdAudit.workspace_id === workspaceId,
        "Safe notification alert was not assigned to the request workspace.",
      );
      assert(
        Number(createdAudit.notification_email_count) === 0 &&
          !createdAudit.slack_webhook_url,
        "Safe notification alert must not have outbound email or Slack channels.",
      );

      const trigger = await triggerAlertThresholdInBackend({
        alertId: created.id,
        currentValue: 1,
      });
      assert(
        trigger.after_count === trigger.before_count + 1,
        "Safe notification trigger did not create exactly one alert log.",
      );
      assert(
        trigger.latest_log?.type === "critical",
        "Safe notification trigger did not create a critical log.",
      );
      assert(
        String(trigger.latest_log?.message || "").includes(alertName),
        "Safe notification log message did not include the alert name.",
      );
      assert(
        trigger.latest_log?.time_window_start &&
          trigger.latest_log?.time_window_end,
        "Safe notification log omitted time window fields.",
      );

      const detail = await client.get(
        apiPath("/tracer/user-alerts/{id}/details/", { id: created.id }),
        { query: { page_number: 0, page_size: 5 } },
      );
      const detailLogs = asArray(detail.logs?.results);
      assert(
        detailLogs.some((log) => log.id === trigger.latest_log.id),
        "Alert detail logs did not include the safe notification log.",
      );

      const logs = await client.get(
        apiPath("/tracer/user-alert-logs/{id}/list/", { id: created.id }),
      );
      assert(
        asArray(logs).some((log) => log.id === trigger.latest_log.id),
        "Direct alert log list did not include the safe notification log.",
      );

      await client.post(apiPath("/tracer/user-alert-logs/resolve/"), {
        log_ids: [trigger.latest_log.id],
      });
      const resolvedAudit = await loadAlertNotificationAudit({
        alertId: created.id,
        logId: trigger.latest_log.id,
      });
      assert(
        resolvedAudit.log_resolved === true,
        "Safe notification log resolve did not persist.",
      );

      await client.delete(apiPath("/tracer/user-alerts/"), {
        body: { ids: [created.id] },
      });
      const deletedAudit = await loadAlertNotificationAudit({
        alertId: created.id,
        logId: trigger.latest_log.id,
      });
      assert(
        deletedAudit.alert_deleted === true,
        "Safe notification alert delete did not set deleted flag.",
      );

      evidence.push({
        project_id: project.id,
        alert_id: created.id,
        alert_name: alertName,
        log_id: trigger.latest_log.id,
        trigger_current_value: trigger.current_value,
        notification_email_count: createdAudit.notification_email_count,
        slack_webhook_configured: Boolean(createdAudit.slack_webhook_url),
        log_resolved: resolvedAudit.log_resolved,
        final_public_deleted: deletedAudit.alert_deleted,
      });
    },
  },
];

function assertObserveProjectListPayload(payload, projects) {
  assert(payload?.metadata, "Observe project list omitted metadata.");
  assert(
    Number(payload.metadata.total_rows) >= projects.length,
    "Observe project metadata total_rows was inconsistent.",
  );
  for (const project of projects) {
    assert(isUuid(project?.id), "Observe project list row omitted a valid id.");
    assert(
      String(project?.name || "").trim(),
      "Observe project list row omitted name.",
    );
    assert(
      Number.isFinite(Number(project.last_30_days_vol)),
      `Observe project ${project.id} omitted numeric last_30_days_vol.`,
    );
    assert(
      Array.isArray(project.daily_volume),
      `Observe project ${project.id} omitted daily_volume array.`,
    );
    assert(
      project.daily_volume.length === 0 || project.daily_volume.length === 30,
      `Observe project ${project.id} daily_volume was not a 30-day series.`,
    );
    assert(
      Number.isFinite(Number(project.run_count)),
      `Observe project ${project.id} omitted numeric run_count.`,
    );
    assert(
      Number.isFinite(Number(project.issues)),
      `Observe project ${project.id} omitted numeric issues.`,
    );
    assert(
      Array.isArray(project.tags),
      `Observe project ${project.id} tags was not an array.`,
    );
  }
}

function assertObserveProjectDetail(
  detail,
  { projectId, organizationId, workspaceId },
) {
  assert(detail?.id === projectId, "Observe project detail id mismatch.");
  assert(
    detail?.trace_type === "observe",
    "Observe project detail trace_type mismatch.",
  );
  assert(
    detail?.organization === organizationId,
    "Observe project detail organization mismatch.",
  );
  assert(
    detail?.workspace === workspaceId,
    "Observe project detail workspace mismatch.",
  );
  assert(
    String(detail?.name || "").trim(),
    "Observe project detail omitted name.",
  );
  assert(
    Array.isArray(detail?.tags),
    "Observe project detail tags was not an array.",
  );
}

function assertSortedObserveProjectNames(projects, direction) {
  const names = projects.map((project) => String(project.name || ""));
  const sorted = [...names].sort((left, right) =>
    left.localeCompare(right, "en-US", {
      sensitivity: "base",
      ignorePunctuation: true,
    }),
  );
  if (direction === "desc") sorted.reverse();
  assert(
    JSON.stringify(names) === JSON.stringify(sorted),
    `Observe projects were not sorted by name ${direction}.`,
  );
}

function assertObserveSdkCodeUsesPlaceholders(payload) {
  const text = JSON.stringify(payload || {});
  assert(
    text.includes("YOUR_FI_API_KEY"),
    "Observe SDK code omitted FI API key placeholder.",
  );
  assert(
    text.includes("YOUR_FI_SECRET_KEY"),
    "Observe SDK code omitted FI secret key placeholder.",
  );
}

function assertErrorFeedListPayload(payload, rows) {
  assert(
    Number.isFinite(Number(payload?.total)),
    "Error Feed list omitted numeric total.",
  );
  assert(
    Number(payload.total) >= rows.length,
    "Error Feed total was smaller than page size.",
  );
  for (const row of rows) {
    assert(
      String(row?.cluster_id || "").trim(),
      "Error Feed row omitted cluster_id.",
    );
    assert(
      ["scanner", "eval"].includes(row.source),
      "Error Feed row source was invalid.",
    );
    assert(
      String(row?.error?.name || "").trim(),
      "Error Feed row omitted error.name.",
    );
    assert(
      ["escalating", "for_review", "acknowledged", "resolved"].includes(
        row.status,
      ),
      "Error Feed row status was invalid.",
    );
    assert(
      ["critical", "high", "medium", "low"].includes(row.severity),
      "Error Feed row severity was invalid.",
    );
    assert(isUuid(row.project_id), "Error Feed row omitted valid project_id.");
    assert(
      Number.isFinite(Number(row.trace_count)),
      "Error Feed row omitted numeric trace_count.",
    );
  }
}

function titleCaseFixLayer(value) {
  return String(value || "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function assertSeveritySorted(rows, direction) {
  const rank = { low: 0, medium: 1, high: 2, critical: 3 };
  const values = rows.map((row) => rank[row.severity] ?? 1);
  const sorted = [...values].sort((left, right) =>
    direction === "desc" ? right - left : left - right,
  );
  assert(
    JSON.stringify(values) === JSON.stringify(sorted),
    `Error Feed severity sort was not ordered ${direction}.`,
  );
}

async function loadErrorFeedDbAudit({ organizationId, workspaceId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(organizationId)} AS organization_id, ${sqlUuid(workspaceId)} AS workspace_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
),
visible_projects AS (
  SELECT project.id
  FROM tracer_project project
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
  WHERE project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id = workspace.id
),
counts AS (
  SELECT
    count(*) FILTER (
      WHERE groups.deleted = false
        AND groups.issue_group IS NOT NULL
        AND groups.project_id IN (SELECT id FROM visible_projects)
    ) AS visible_error_group_count,
    (
      SELECT hidden_groups.cluster_id
      FROM tracer_trace_error_group hidden_groups
      JOIN tracer_project hidden_project ON hidden_project.id = hidden_groups.project_id
      CROSS JOIN requested r
      WHERE hidden_groups.deleted = false
        AND hidden_groups.issue_group IS NOT NULL
        AND hidden_project.organization_id = r.organization_id
        AND hidden_groups.project_id NOT IN (SELECT id FROM visible_projects)
      ORDER BY hidden_groups.created_at DESC
      LIMIT 1
    ) AS hidden_cluster_id
  FROM tracer_trace_error_group groups
)
SELECT json_build_object(
  'visible_error_group_count', visible_error_group_count,
  'hidden_cluster_id', hidden_cluster_id
)
FROM counts;
`;
  return runPostgresJson(sql);
}

async function loadErrorFeedMutationDbAudit({
  organizationId,
  workspaceId,
  clusterId,
  traceId,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for feed audit.",
  );
  assert(isUuid(workspaceId), "workspaceId must be a UUID for feed audit.");
  assert(isUuid(traceId), "traceId must be a UUID for feed audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlString(clusterId)} AS cluster_id,
    ${sqlUuid(traceId)} AS trace_id
),
cluster_row AS (
  SELECT
    groups.id,
    groups.cluster_id,
    groups.project_id,
    groups.status,
    groups.priority,
    groups.assignee_id,
    assignee.email AS assignee_email,
    groups.external_issue_url,
    groups.external_issue_id,
    project.workspace_id,
    project.organization_id,
    groups.deleted
  FROM tracer_trace_error_group groups
  JOIN tracer_project project ON project.id = groups.project_id
  LEFT JOIN accounts_user assignee ON assignee.id = groups.assignee_id
  JOIN requested r ON groups.cluster_id = r.cluster_id
  WHERE groups.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id = r.workspace_id
  ORDER BY groups.created_at DESC
  LIMIT 1
),
trace_row AS (
  SELECT
    trace.id,
    trace.project_id,
    trace.error_analysis_status
  FROM tracer_trace trace
  JOIN requested r ON trace.id = r.trace_id
  WHERE trace.deleted = false
)
SELECT json_build_object(
  'cluster_visible', EXISTS (SELECT 1 FROM cluster_row),
  'trace_visible', EXISTS (
    SELECT 1
    FROM trace_row trace
    JOIN cluster_row cluster ON cluster.project_id = trace.project_id
  ),
  'cluster_id', (SELECT cluster_id FROM cluster_row),
  'cluster_pk', (SELECT id::text FROM cluster_row),
  'project_id', (SELECT project_id::text FROM cluster_row),
  'workspace_id', (SELECT workspace_id::text FROM cluster_row),
  'organization_id', (SELECT organization_id::text FROM cluster_row),
  'status', (SELECT status FROM cluster_row),
  'priority', (SELECT priority FROM cluster_row),
  'assignee_id', (SELECT assignee_id::text FROM cluster_row),
  'assignee_email', (SELECT assignee_email FROM cluster_row),
  'external_issue_url', (SELECT external_issue_url FROM cluster_row),
  'external_issue_id', (SELECT external_issue_id FROM cluster_row),
  'trace_id', (SELECT id::text FROM trace_row),
  'trace_error_analysis_status', (SELECT error_analysis_status FROM trace_row)
);
`;
  return runPostgresJson(sql);
}

async function restoreErrorFeedMutationDbState({ before }) {
  assert(before?.cluster_id, "Feed restore requires original cluster_id.");
  assert(
    isUuid(before?.project_id),
    "Feed restore requires original project_id.",
  );
  assert(isUuid(before?.trace_id), "Feed restore requires original trace_id.");
  const sql = `
WITH restored_cluster AS (
  UPDATE tracer_trace_error_group
  SET
    status = ${sqlString(before.status || "escalating")},
    priority = ${sqlString(before.priority || "medium")},
    assignee_id = ${sqlUuidOrNull(before.assignee_id)},
    external_issue_url = ${sqlStringOrNull(before.external_issue_url)},
    external_issue_id = ${sqlStringOrNull(before.external_issue_id)},
    updated_at = NOW()
  WHERE cluster_id = ${sqlString(before.cluster_id)}
    AND project_id = ${sqlUuid(before.project_id)}
  RETURNING status, priority, assignee_id, external_issue_url, external_issue_id
),
restored_trace AS (
  UPDATE tracer_trace
  SET error_analysis_status = ${sqlString(before.trace_error_analysis_status || "pending")}
  WHERE id = ${sqlUuid(before.trace_id)}
  RETURNING error_analysis_status
)
SELECT json_build_object(
  'status', (SELECT status FROM restored_cluster),
  'priority', (SELECT priority FROM restored_cluster),
  'assignee_id', (SELECT assignee_id::text FROM restored_cluster),
  'external_issue_url', (SELECT external_issue_url FROM restored_cluster),
  'external_issue_id', (SELECT external_issue_id FROM restored_cluster),
  'trace_error_analysis_status', (SELECT error_analysis_status FROM restored_trace)
);
`;
  return runPostgresJson(sql);
}

async function seedErrorFeedDeepAnalysisCache({
  analysisId,
  traceId,
  projectId,
}) {
  assert(
    isUuid(analysisId),
    "analysisId must be a UUID for feed analysis seed.",
  );
  assert(isUuid(traceId), "traceId must be a UUID for feed analysis seed.");
  assert(isUuid(projectId), "projectId must be a UUID for feed analysis seed.");
  const sql = `
WITH updated_trace AS (
  UPDATE tracer_trace
  SET error_analysis_status = 'completed'
  WHERE id = ${sqlUuid(traceId)}
  RETURNING id, error_analysis_status
),
inserted AS (
  INSERT INTO tracer_trace_error_analysis (
    id,
    created_at,
    updated_at,
    deleted,
    trace_id,
    project_id,
    analysis_date,
    agent_version,
    memory_enhanced,
    total_errors,
    high_impact_errors,
    medium_impact_errors,
    low_impact_errors,
    recommended_priority,
    memory_context,
    grouped_errors_count
  )
  VALUES (
    ${sqlUuid(analysisId)},
    NOW(),
    NOW(),
    false,
    ${sqlUuid(traceId)},
    ${sqlUuid(projectId)},
    NOW(),
    'api-journey',
    false,
    0,
    0,
    0,
    0,
    'LOW',
    '{}'::jsonb,
    0
  )
  RETURNING id
)
SELECT json_build_object(
  'analysis_id', (SELECT id::text FROM inserted),
  'trace_id', (SELECT id::text FROM updated_trace),
  'trace_error_analysis_status', (SELECT error_analysis_status FROM updated_trace)
);
`;
  return runPostgresJson(sql);
}

async function deleteErrorFeedDeepAnalysisCache({ analysisId, before }) {
  assert(
    isUuid(analysisId),
    "analysisId must be a UUID for feed analysis cleanup.",
  );
  assert(
    isUuid(before?.trace_id),
    "Feed analysis cleanup requires original trace id.",
  );
  const sql = `
WITH deleted_details AS (
  DELETE FROM tracer_trace_error_detail
  WHERE analysis_id = ${sqlUuid(analysisId)}
  RETURNING id
),
deleted_analysis AS (
  DELETE FROM tracer_trace_error_analysis
  WHERE id = ${sqlUuid(analysisId)}
  RETURNING id
),
restored_trace AS (
  UPDATE tracer_trace
  SET error_analysis_status = ${sqlString(before.trace_error_analysis_status || "pending")}
  WHERE id = ${sqlUuid(before.trace_id)}
  RETURNING error_analysis_status
)
SELECT json_build_object(
  'deleted_detail_count', (SELECT count(*) FROM deleted_details),
  'deleted_analysis_count', (SELECT count(*) FROM deleted_analysis),
  'trace_error_analysis_status', (SELECT error_analysis_status FROM restored_trace)
);
`;
  return runPostgresJson(sql);
}

async function loadTraceErrorTaskDbState({
  organizationId,
  workspaceId,
  projectId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id
),
project_row AS (
  SELECT project.id, project.name, project.organization_id, project.workspace_id
  FROM tracer_project project
  JOIN requested r ON project.id = r.project_id
  WHERE project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id = r.workspace_id
),
task_row AS (
  SELECT task.*
  FROM tracer_trace_error_analysis_task task
  JOIN project_row project ON project.id = task.project_id
  ORDER BY task.created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'project_visible', EXISTS (SELECT 1 FROM project_row),
  'project_name', (SELECT name FROM project_row),
  'task_row_exists', EXISTS (SELECT 1 FROM task_row),
  'task_exists', EXISTS (SELECT 1 FROM task_row WHERE deleted = false),
  'task_id', (SELECT id FROM task_row),
  'sampling_rate', (SELECT sampling_rate FROM task_row),
  'status', (SELECT status FROM task_row),
  'task_deleted', (SELECT deleted FROM task_row),
  'task_deleted_at', (SELECT deleted_at FROM task_row),
  'total_traces_analyzed', (SELECT total_traces_analyzed FROM task_row),
  'total_errors_found', (SELECT total_errors_found FROM task_row),
  'failed_analyses', (SELECT failed_analyses FROM task_row),
  'active_task_count', (
    SELECT count(*)
    FROM tracer_trace_error_analysis_task task
    JOIN project_row project ON project.id = task.project_id
    WHERE task.deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

async function restoreTraceErrorTaskDbState({ projectId, before }) {
  const restoreCte = before.task_row_exists
    ? `
restored AS (
UPDATE tracer_trace_error_analysis_task
SET sampling_rate = ${sqlNumber(before.sampling_rate)},
    status = ${sqlTraceTaskStatus(before.status)},
    deleted = ${before.task_deleted ? "true" : "false"},
    deleted_at = ${before.task_deleted ? sqlTimestampOrNull(before.task_deleted_at) : "NULL"},
    updated_at = NOW()
WHERE id = ${sqlUuid(before.task_id)}
RETURNING id
)
`
    : `
restored AS (
UPDATE tracer_trace_error_analysis_task
SET deleted = true,
    deleted_at = COALESCE(deleted_at, NOW()),
    updated_at = NOW()
WHERE project_id = ${sqlUuid(projectId)}
  AND deleted = false
RETURNING id
)
`;
  const restoreSql = `
WITH ${restoreCte}
SELECT json_build_object(
  'restored_existing', ${before.task_row_exists ? "true" : "false"},
  'touched_rows', (SELECT count(*) FROM restored)
);
`;
  const restored = await runPostgresJson(restoreSql);
  const auditSql = `
SELECT json_build_object(
  'active_task_count', (
    SELECT count(*)
    FROM tracer_trace_error_analysis_task
    WHERE project_id = ${sqlUuid(projectId)}
      AND deleted = false
  )
);
`;
  const audit = await runPostgresJson(auditSql);
  return { ...restored, ...audit };
}

async function loadEvalTaskDbAudit({ organizationId, workspaceId, taskId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(taskId)} AS task_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
task_row AS (
  SELECT task.id, task.project_id, project.workspace_id, project.organization_id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN requested r ON task.id = r.task_id
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
),
org_tasks_with_evals AS (
  SELECT DISTINCT task.id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN tracer_eval_task_evals task_evals ON task_evals.evaltask_id = task.id
  CROSS JOIN requested r
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
),
workspace_tasks_with_evals AS (
  SELECT DISTINCT task.id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN tracer_eval_task_evals task_evals ON task_evals.evaltask_id = task.id
  CROSS JOIN requested r
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id = r.workspace_id
),
null_workspace_tasks_with_evals AS (
  SELECT DISTINCT task.id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN tracer_eval_task_evals task_evals ON task_evals.evaltask_id = task.id
  CROSS JOIN requested r
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id IS NULL
),
other_workspace_tasks_with_evals AS (
  SELECT DISTINCT task.id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN tracer_eval_task_evals task_evals ON task_evals.evaltask_id = task.id
  CROSS JOIN requested r
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.workspace_id IS NOT NULL
    AND project.workspace_id <> r.workspace_id
),
visible_tasks_with_evals AS (
  SELECT DISTINCT task.id
  FROM tracer_eval_task task
  JOIN tracer_project project ON project.id = task.project_id
  JOIN tracer_eval_task_evals task_evals ON task_evals.evaltask_id = task.id
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
  WHERE task.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND (
      project.workspace_id = r.workspace_id
      OR (
        workspace.is_default = true
        AND (
          project.workspace_id IS NULL
          OR project.workspace_id IN (
            SELECT default_workspace.id
            FROM accounts_workspace default_workspace
            WHERE default_workspace.organization_id = r.organization_id
              AND default_workspace.is_default = true
          )
        )
      )
    )
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'task_visible', EXISTS (SELECT 1 FROM task_row),
  'selected_project_id', (SELECT project_id FROM task_row),
  'selected_workspace_id', (SELECT workspace_id FROM task_row),
  'selected_eval_count', (
    SELECT count(*)
    FROM tracer_eval_task_evals task_evals
    JOIN task_row task ON task.id = task_evals.evaltask_id
  ),
  'selected_log_count', (
    SELECT count(*)
    FROM tracer_eval_logger log
    JOIN task_row task ON log.eval_task_id = task.id::text
  ),
  'org_task_with_evals_count', (SELECT count(*) FROM org_tasks_with_evals),
  'workspace_task_with_evals_count', (SELECT count(*) FROM workspace_tasks_with_evals),
  'null_workspace_task_with_evals_count', (SELECT count(*) FROM null_workspace_tasks_with_evals),
  'other_workspace_task_with_evals_count', (SELECT count(*) FROM other_workspace_tasks_with_evals),
  'visible_task_with_evals_count', (SELECT count(*) FROM visible_tasks_with_evals)
);
`;
  return runPostgresJson(sql);
}

async function loadForeignEvalConfigCandidate({
  organizationId,
  workspaceId,
  projectId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
visible_projects AS (
  SELECT project.id, project.name
  FROM tracer_project project
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
  WHERE project.deleted = false
    AND project.organization_id = r.organization_id
    AND project.id <> r.project_id
    AND (
      project.workspace_id = r.workspace_id
      OR (
        workspace.is_default = true
        AND (
          project.workspace_id IS NULL
          OR project.workspace_id IN (
            SELECT default_workspace.id
            FROM accounts_workspace default_workspace
            WHERE default_workspace.organization_id = r.organization_id
              AND default_workspace.is_default = true
          )
        )
      )
    )
),
candidate AS (
  SELECT config.id, config.project_id, visible_projects.name AS project_name
  FROM tracer_custom_eval_config config
  JOIN visible_projects ON visible_projects.id = config.project_id
  WHERE config.deleted = false
  ORDER BY config.created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'config_id', (SELECT id FROM candidate),
  'config_project_id', (SELECT project_id FROM candidate),
  'config_project_name', (SELECT project_name FROM candidate)
);
`;
  return runPostgresJson(sql);
}

async function resolveEvalTaskSeedWithTrace(client, evidence) {
  const list = await client.get(
    apiPath("/tracer/eval-task/list_eval_tasks_with_project_name/"),
    {
      query: {
        page_number: 0,
        page_size: 25,
        sort_params: JSON.stringify([
          { column_id: "created_at", direction: "desc" },
        ]),
      },
    },
  );
  const rows = asArray(list.table || list).filter(
    (row) => row?.id && (row.project_id || row.filters_applied?.project_id),
  );
  if (!rows.length) {
    skip("No Observe task exists to provide a project/eval config seed.");
  }

  for (const seed of rows) {
    const projectId = seed.project_id || seed.filters_applied?.project_id;
    if (!isUuid(projectId)) continue;
    const seedDetail = await client.get(
      apiPath("/tracer/eval-task/get_eval_details/"),
      {
        query: { eval_id: seed.id },
      },
    );
    const seedEval = asArray(seedDetail.evals_applied)[0];
    if (!seedEval?.id) continue;

    const traceList = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_traces_of_session/"), [], {
        project_id: projectId,
        page_number: 0,
        page_size: 10,
      }),
    );
    const traces = asArray(traceList).filter((row) => row?.trace_id || row?.id);
    for (const trace of traces) {
      const traceId = trace.trace_id || trace.id;
      if (!isUuid(traceId)) continue;
      try {
        const traceDetail = await client.get(
          apiPath("/tracer/trace/{id}/", { id: traceId }),
        );
        evidence.push({
          endpoint: "eval task linked-source seed",
          seed_task_id: seed.id,
          seed_project_id: projectId,
          seed_eval_config_id: seedEval.id,
          trace_id: traceId,
          trace_total_rows: traceList?.metadata?.total_rows ?? null,
        });
        return { seed, seedEval, projectId, trace, traceDetail };
      } catch (error) {
        if (
          !(
            error instanceof ApiJourneyError &&
            [400, 404].includes(error.status)
          )
        ) {
          throw error;
        }
      }
    }
  }

  skip(
    "No Observe task project with a readable trace exists for linked-source coverage.",
  );
}

async function setEvalTaskStatus({ taskId, status }) {
  const sql = `
WITH updated AS (
  UPDATE tracer_eval_task
  SET status = ${sqlEvalTaskStatus(status)},
      updated_at = NOW()
  WHERE id = ${sqlUuid(taskId)}
  RETURNING id, status
)
SELECT json_build_object(
  'updated_count', (SELECT count(*) FROM updated),
  'status', (SELECT status FROM updated)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.updated_count) === 1,
    "Eval task status seed update failed.",
  );
  return result;
}

async function loadEvalTaskLifecycleAudit({ taskId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(taskId)} AS task_id
),
task_row AS (
  SELECT *
  FROM tracer_eval_task task
  JOIN requested r ON task.id = r.task_id
)
SELECT json_build_object(
  'task_exists', EXISTS (SELECT 1 FROM task_row),
  'task_deleted', (SELECT deleted FROM task_row),
  'task_status', (SELECT status FROM task_row),
  'task_name', (SELECT name FROM task_row),
  'sampling_rate', (SELECT sampling_rate FROM task_row),
  'eval_count', (
    SELECT count(*)
    FROM tracer_eval_task_evals task_evals
    JOIN requested r ON task_evals.evaltask_id = r.task_id
  ),
  'task_logger_count', (
    SELECT count(*)
    FROM tracer_eval_task_logger task_logger
    JOIN requested r ON task_logger.eval_task_id = r.task_id
  ),
  'eval_log_count', (
    SELECT count(*)
    FROM tracer_eval_logger log
    JOIN requested r ON log.eval_task_id = r.task_id::text
  )
);
`;
  return runPostgresJson(sql);
}

async function loadEvalTaskLinkedSourceAudit({
  organizationId,
  workspaceId,
  taskId,
  projectId,
  traceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(taskId)} AS task_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(traceId)} AS trace_id
),
task_row AS (
  SELECT task.id, task.project_id, task.filters, task.deleted
  FROM tracer_eval_task task
  JOIN requested r ON task.id = r.task_id
  WHERE task.project_id = r.project_id
),
source_trace AS (
  SELECT trace.id, trace.project_id
  FROM tracer_trace trace
  JOIN requested r ON trace.id = r.trace_id
  WHERE trace.project_id = r.project_id
    AND trace.deleted = false
)
SELECT json_build_object(
  'task_exists', EXISTS (SELECT 1 FROM task_row),
  'task_deleted', COALESCE((SELECT deleted FROM task_row), false),
  'task_project_id', (SELECT project_id FROM task_row),
  'filters', (SELECT filters FROM task_row),
  'trace_id_filter_contains', COALESCE((
    SELECT (filters::jsonb -> 'trace_id') ? (SELECT trace_id::text FROM requested)
    FROM task_row
  ), false),
  'eval_count', (
    SELECT count(*)
    FROM tracer_eval_task_evals task_evals
    JOIN requested r ON task_evals.evaltask_id = r.task_id
  ),
  'source_trace_exists', EXISTS (SELECT 1 FROM source_trace),
  'source_trace_project_id', (SELECT project_id FROM source_trace),
  'matched_span_count', (
    SELECT count(*)
    FROM tracer_observation_span span
    JOIN requested r ON span.project_id = r.project_id
    WHERE span.trace_id = r.trace_id
      AND span.deleted = false
  ),
  'source_reverse_task_column_count', (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'tracer_eval_task'
      AND column_name IN ('source_type', 'source_id')
  )
);
`;
  return runPostgresJson(sql);
}

async function deleteEvalTaskDbArtifacts({ taskId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(taskId)} AS task_id
),
deleted_task_evals AS (
  DELETE FROM tracer_eval_task_evals
  WHERE evaltask_id = (SELECT task_id FROM requested)
  RETURNING evaltask_id
),
deleted_task_loggers AS (
  DELETE FROM tracer_eval_task_logger
  WHERE eval_task_id = (SELECT task_id FROM requested)
  RETURNING id
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger
  WHERE eval_task_id = (SELECT task_id::text FROM requested)
  RETURNING id
),
deleted_tasks AS (
  DELETE FROM tracer_eval_task
  WHERE id = (SELECT task_id FROM requested)
  RETURNING id
)
SELECT json_build_object(
  'deleted_task_eval_rows', (SELECT count(*) FROM deleted_task_evals),
  'deleted_task_logger_rows', (SELECT count(*) FROM deleted_task_loggers),
  'deleted_eval_log_rows', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_task_rows', (SELECT count(*) FROM deleted_tasks)
);
`;
  return runPostgresJson(sql);
}

async function loadObserveProjectListDbAudit({ organizationId, workspaceId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(organizationId)} AS organization_id, ${sqlUuid(workspaceId)} AS workspace_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
),
project_counts AS (
  SELECT
    count(*) FILTER (
      WHERE project.deleted = false
        AND project.trace_type = 'observe'
        AND project.organization_id = r.organization_id
    ) AS org_observe_project_count,
    count(*) FILTER (
      WHERE project.deleted = false
        AND project.trace_type = 'observe'
        AND project.organization_id = r.organization_id
        AND project.workspace_id = r.workspace_id
    ) AS workspace_observe_project_count,
    count(*) FILTER (
      WHERE project.deleted = false
        AND project.trace_type = 'observe'
        AND project.organization_id = r.organization_id
        AND project.workspace_id IS NULL
    ) AS null_workspace_observe_project_count,
    count(*) FILTER (
      WHERE project.deleted = false
        AND project.trace_type = 'observe'
        AND project.organization_id = r.organization_id
        AND project.workspace_id IS NOT NULL
        AND project.workspace_id <> r.workspace_id
    ) AS other_workspace_observe_project_count,
    count(*) FILTER (
      WHERE project.deleted = false
        AND project.trace_type = 'observe'
        AND project.organization_id = r.organization_id
        AND (
          (workspace.is_default = true AND (
            project.workspace_id = r.workspace_id
            OR project.workspace_id IS NULL
            OR project.workspace_id IN (
              SELECT default_workspace.id
              FROM accounts_workspace default_workspace
              WHERE default_workspace.organization_id = r.organization_id
                AND default_workspace.is_default = true
            )
          ))
          OR (workspace.is_default = false AND project.workspace_id = r.workspace_id)
        )
    ) AS visible_observe_project_count
  FROM tracer_project project
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'org_observe_project_count', org_observe_project_count,
  'workspace_observe_project_count', workspace_observe_project_count,
  'null_workspace_observe_project_count', null_workspace_observe_project_count,
  'other_workspace_observe_project_count', other_workspace_observe_project_count,
  'visible_observe_project_count', visible_observe_project_count
)
FROM project_counts;
`;
  return runPostgresJson(sql);
}

async function loadDashboardDbAudit({
  organizationId,
  workspaceId,
  dashboardId,
  widgetIds,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for dashboard audit.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for dashboard audit.",
  );
  assert(
    isUuid(dashboardId),
    "dashboardId must be a UUID for dashboard audit.",
  );
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(dashboardId)} AS dashboard_id,
    ${sqlUuidArray(widgetIds)} AS widget_ids
),
dashboard_row AS (
  SELECT
    d.id,
    d.name,
    d.description,
    d.workspace_id,
    ws.organization_id,
    d.deleted,
    d.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_dashboard d
  JOIN accounts_workspace ws ON ws.id = d.workspace_id
  JOIN requested r ON d.id = r.dashboard_id
),
widget_rows AS (
  SELECT
    w.id,
    w.dashboard_id,
    w.name,
    w.position,
    w.width,
    w.height,
    w.deleted,
    w.deleted_at IS NOT NULL AS deleted_at_set,
    w.query_config,
    w.chart_config
  FROM tracer_dashboardwidget w
  JOIN requested r ON w.id = ANY(r.widget_ids)
)
SELECT json_build_object(
  'dashboard_id', (SELECT id::text FROM dashboard_row),
  'dashboard_name', (SELECT name FROM dashboard_row),
  'dashboard_description', (SELECT description FROM dashboard_row),
  'dashboard_workspace_id', (SELECT workspace_id::text FROM dashboard_row),
  'dashboard_organization_id', (SELECT organization_id::text FROM dashboard_row),
  'dashboard_deleted', COALESCE((SELECT deleted FROM dashboard_row), false),
  'dashboard_deleted_at_set', COALESCE((SELECT deleted_at_set FROM dashboard_row), false),
  'active_widget_count', (
    SELECT count(*)
    FROM tracer_dashboardwidget w
    JOIN requested r ON w.dashboard_id = r.dashboard_id
    WHERE w.deleted = false
  ),
  'widgets', COALESCE((
    SELECT json_agg(json_build_object(
      'id', id::text,
      'dashboard_id', dashboard_id::text,
      'name', name,
      'position', position,
      'width', width,
      'height', height,
      'deleted', deleted,
      'deleted_at_set', deleted_at_set,
      'metric_count', jsonb_array_length(COALESCE(query_config::jsonb -> 'metrics', '[]'::jsonb)),
      'chart_type', chart_config::jsonb ->> 'chart_type'
    ) ORDER BY id::text)
    FROM widget_rows
  ), '[]'::json)
)
FROM requested;
`;
  return runPostgresJson(sql);
}

async function loadObservationSpanDbAudit({
  organizationId,
  workspaceId,
  projectId,
  spanId,
  traceId,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for span audit.",
  );
  assert(isUuid(workspaceId), "workspaceId must be a UUID for span audit.");
  assert(isUuid(projectId), "projectId must be a UUID for span audit.");
  assert(isUuid(traceId), "traceId must be a UUID for span audit.");
  assert(String(spanId || "").trim(), "spanId must be set for span audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlString(spanId)} AS span_id,
    ${sqlUuid(traceId)} AS trace_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
visible_spans AS (
  SELECT
    span.id,
    span.trace_id::text AS trace_id,
    span.project_id::text AS project_id,
    span.project_version_id::text AS project_version_id,
    span.parent_span_id,
    span.name,
    COALESCE(span.tags::jsonb, '[]'::jsonb) AS tags,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_observation_span span
  JOIN tracer_project project ON project.id = span.project_id
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
  WHERE span.deleted = false
    AND project.deleted = false
    AND project.organization_id = r.organization_id
    AND span.project_id = r.project_id
    AND (
      project.workspace_id = r.workspace_id
      OR (
        workspace.is_default = true
        AND (
          project.workspace_id IS NULL
          OR project.workspace_id IN (
            SELECT default_workspace.id
            FROM accounts_workspace default_workspace
            WHERE default_workspace.organization_id = r.organization_id
              AND default_workspace.is_default = true
          )
        )
      )
    )
),
selected_span AS (
  SELECT span.*
  FROM visible_spans span
  JOIN requested r ON span.id = r.span_id
),
root_span AS (
  SELECT span.id
  FROM visible_spans span
  JOIN requested r ON span.trace_id = r.trace_id::text
  WHERE span.parent_span_id IS NULL
  ORDER BY span.id
  LIMIT 1
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'visible_span_count', (SELECT count(*) FROM visible_spans),
  'selected_span', COALESCE((SELECT row_to_json(selected_span) FROM selected_span), '{}'::json),
  'selected_tags', COALESCE((SELECT tags FROM selected_span), '[]'::jsonb),
  'root_span_id', (SELECT id FROM root_span)
);
`;
  return runPostgresJson(sql);
}

async function loadObservationSpanMutationDbAudit({
  organizationId,
  workspaceId,
  projectId,
  spanIds,
  labelId,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for span mutation audit.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for span mutation audit.",
  );
  assert(
    isUuid(projectId),
    "projectId must be a UUID for span mutation audit.",
  );
  assert(
    asArray(spanIds).length > 0,
    "spanIds must be set for span mutation audit.",
  );
  assert(isUuid(labelId), "labelId must be a UUID for span mutation audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlStringArray(spanIds)} AS span_ids,
    ${sqlUuid(labelId)} AS label_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
span_rows AS (
  SELECT
    span.id,
    span.name,
    span.status_message,
    span.metadata,
    span.trace_id::text AS trace_id,
    span.project_id::text AS project_id,
    span.project_version_id::text AS project_version_id,
    span.deleted,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_observation_span span
  JOIN tracer_project project ON project.id = span.project_id
  JOIN requested r ON span.id = ANY(r.span_ids)
  WHERE project.organization_id = r.organization_id
),
label_row AS (
  SELECT
    label.id::text AS id,
    label.name,
    label.deleted,
    label.deleted_at IS NOT NULL AS deleted_at_set,
    label.workspace_id::text AS workspace_id,
    label.organization_id::text AS organization_id,
    label.project_id::text AS project_id
  FROM model_hub_annotationslabels label
  JOIN requested r ON label.id = r.label_id
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'spans', COALESCE((SELECT json_agg(row_to_json(span_rows) ORDER BY id) FROM span_rows), '[]'::json),
  'label', COALESCE((SELECT row_to_json(label_row) FROM label_row), '{}'::json)
);
`;
  return runPostgresJson(sql);
}

async function loadTraceAnnotationDbAudit({
  organizationId,
  workspaceId,
  projectId,
  spanId,
  traceId,
  labelId,
  userId,
  noteText,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for trace annotation audit.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for trace annotation audit.",
  );
  assert(
    isUuid(projectId),
    "projectId must be a UUID for trace annotation audit.",
  );
  assert(
    String(spanId || "").trim(),
    "spanId must be set for trace annotation audit.",
  );
  assert(isUuid(traceId), "traceId must be a UUID for trace annotation audit.");
  assert(isUuid(labelId), "labelId must be a UUID for trace annotation audit.");
  assert(isUuid(userId), "userId must be a UUID for trace annotation audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlString(spanId)} AS span_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlUuid(labelId)} AS label_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlString(noteText)} AS note_text
),
score_rows AS (
  SELECT
    score.id::text AS id,
    score.observation_span_id,
    score.label_id::text AS label_id,
    score.annotator_id::text AS annotator_id,
    score.organization_id::text AS organization_id,
    score.workspace_id::text AS workspace_id,
    score.value,
    score.deleted
  FROM model_hub_score score
  JOIN requested r ON score.observation_span_id = r.span_id
    AND score.label_id = r.label_id
    AND score.annotator_id = r.user_id
),
note_rows AS (
  SELECT
    note.id::text AS id,
    note.span_id,
    note.created_by_annotator,
    note.notes,
    note.deleted
  FROM tracer_spannotes note
  JOIN requested r ON note.span_id = r.span_id
    AND note.created_by_annotator = r.user_id::text
    AND note.notes = r.note_text
),
legacy_rows AS (
  SELECT annotation.id::text AS id
  FROM trace_annotation annotation
  JOIN requested r ON annotation.annotation_label_id = r.label_id
    AND (
      annotation.trace_id = r.trace_id
      OR annotation.observation_span_id = r.span_id
    )
)
SELECT json_build_object(
  'active_score_count', (
    SELECT count(*) FROM score_rows WHERE deleted = false
  ),
  'score_count', (SELECT count(*) FROM score_rows),
  'score_ids', COALESCE((
    SELECT json_agg(id ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'score_workspace_ids', COALESCE((
    SELECT json_agg(workspace_id ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'score_values', COALESCE((
    SELECT json_agg(value ORDER BY id) FROM score_rows WHERE deleted = false
  ), '[]'::json),
  'active_note_count', (
    SELECT count(*) FROM note_rows WHERE deleted = false
  ),
  'note_ids', COALESCE((
    SELECT json_agg(id ORDER BY id) FROM note_rows WHERE deleted = false
  ), '[]'::json),
  'legacy_trace_annotation_count', (SELECT count(*) FROM legacy_rows)
);
`;
  return runPostgresJson(sql);
}

async function loadTraceLifecycleDbAudit({
  organizationId,
  workspaceId,
  projectId,
  projectVersionId,
  traceIds,
  spanIds,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for trace audit.",
  );
  assert(isUuid(workspaceId), "workspaceId must be a UUID for trace audit.");
  assert(isUuid(projectId), "projectId must be a UUID for trace audit.");
  assert(
    isUuid(projectVersionId),
    "projectVersionId must be a UUID for trace audit.",
  );
  assert(asArray(traceIds).length > 0, "traceIds must be set for trace audit.");
  assert(asArray(spanIds).length > 0, "spanIds must be set for trace audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
project_version_row AS (
  SELECT
    pv.id::text AS id,
    pv.project_id::text AS project_id,
    pv.deleted,
    pv.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_project_version pv
  JOIN requested r ON pv.id = r.project_version_id
),
trace_rows AS (
  SELECT
    trace.id::text AS id,
    trace.name,
    trace.metadata,
    trace.tags,
    trace.project_id::text AS project_id,
    trace.project_version_id::text AS project_version_id,
    trace.deleted,
    trace.deleted_at IS NOT NULL AS deleted_at_set,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_trace trace
  JOIN tracer_project project ON project.id = trace.project_id
  JOIN requested r ON trace.id = ANY(r.trace_ids)
  WHERE project.organization_id = r.organization_id
),
span_rows AS (
  SELECT
    span.id,
    span.trace_id::text AS trace_id,
    span.project_id::text AS project_id,
    span.project_version_id::text AS project_version_id,
    span.deleted,
    span.deleted_at IS NOT NULL AS deleted_at_set,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_observation_span span
  JOIN tracer_project project ON project.id = span.project_id
  JOIN requested r ON span.id = ANY(r.span_ids)
  WHERE project.organization_id = r.organization_id
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'project_version', COALESCE((SELECT row_to_json(project_version_row) FROM project_version_row), '{}'::json),
  'traces', COALESCE((SELECT json_agg(row_to_json(trace_rows) ORDER BY id) FROM trace_rows), '[]'::json),
  'spans', COALESCE((SELECT json_agg(row_to_json(span_rows) ORDER BY id) FROM span_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function loadTraceSessionLifecycleDbAudit({
  organizationId,
  workspaceId,
  projectId,
  projectVersionId,
  sessionId,
  traceIds,
  spanIds,
  evalLogIds,
}) {
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for session audit.",
  );
  assert(isUuid(workspaceId), "workspaceId must be a UUID for session audit.");
  assert(isUuid(projectId), "projectId must be a UUID for session audit.");
  assert(
    isUuid(projectVersionId),
    "projectVersionId must be a UUID for session audit.",
  );
  assert(isUuid(sessionId), "sessionId must be a UUID for session audit.");
  assert(
    asArray(traceIds).length > 0,
    "traceIds must be set for session audit.",
  );
  assert(asArray(spanIds).length > 0, "spanIds must be set for session audit.");
  assert(
    asArray(evalLogIds).length > 0,
    "evalLogIds must be set for session audit.",
  );
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id,
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids,
    ${sqlUuidArray(evalLogIds)} AS eval_log_ids
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
project_version_row AS (
  SELECT
    pv.id::text AS id,
    pv.project_id::text AS project_id,
    pv.deleted,
    pv.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_project_version pv
  JOIN requested r ON pv.id = r.project_version_id
),
session_row AS (
  SELECT
    session.id::text AS id,
    session.name,
    session.project_id::text AS project_id,
    session.deleted,
    session.deleted_at IS NOT NULL AS deleted_at_set,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM trace_session session
  JOIN tracer_project project ON project.id = session.project_id
  JOIN requested r ON session.id = r.session_id
  WHERE project.organization_id = r.organization_id
),
trace_rows AS (
  SELECT
    trace.id::text AS id,
    trace.session_id::text AS session_id,
    trace.project_id::text AS project_id,
    trace.project_version_id::text AS project_version_id,
    trace.deleted,
    trace.deleted_at IS NOT NULL AS deleted_at_set,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_trace trace
  JOIN tracer_project project ON project.id = trace.project_id
  JOIN requested r ON trace.id = ANY(r.trace_ids)
  WHERE project.organization_id = r.organization_id
),
span_rows AS (
  SELECT
    span.id,
    span.trace_id::text AS trace_id,
    span.project_id::text AS project_id,
    span.project_version_id::text AS project_version_id,
    span.deleted,
    span.deleted_at IS NOT NULL AS deleted_at_set,
    project.workspace_id::text AS workspace_id,
    project.organization_id::text AS organization_id
  FROM tracer_observation_span span
  JOIN tracer_project project ON project.id = span.project_id
  JOIN requested r ON span.id = ANY(r.span_ids)
  WHERE project.organization_id = r.organization_id
),
eval_log_rows AS (
  SELECT
    eval_log.id::text AS id,
    eval_log.trace_session_id::text AS trace_session_id,
    eval_log.trace_id::text AS trace_id,
    eval_log.target_type,
    eval_log.deleted,
    eval_log.deleted_at IS NOT NULL AS deleted_at_set
  FROM tracer_eval_logger eval_log
  JOIN requested r ON eval_log.id = ANY(r.eval_log_ids)
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'project_version', COALESCE((SELECT row_to_json(project_version_row) FROM project_version_row), '{}'::json),
  'session', COALESCE((SELECT row_to_json(session_row) FROM session_row), '{}'::json),
  'traces', COALESCE((SELECT json_agg(row_to_json(trace_rows) ORDER BY id) FROM trace_rows), '[]'::json),
  'spans', COALESCE((SELECT json_agg(row_to_json(span_rows) ORDER BY id) FROM span_rows), '[]'::json),
  'eval_logs', COALESCE((SELECT json_agg(row_to_json(eval_log_rows) ORDER BY id) FROM eval_log_rows), '[]'::json)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteTraceLifecycleArtifacts({
  projectId,
  projectVersionId,
  traceIds,
  spanIds,
}) {
  assert(isUuid(projectId), "projectId must be a UUID for cleanup.");
  assert(
    isUuid(projectVersionId),
    "projectVersionId must be a UUID for cleanup.",
  );
  assert(asArray(traceIds).length > 0, "traceIds must be set for cleanup.");
  assert(asArray(spanIds).length > 0, "spanIds must be set for cleanup.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger eval_log
  USING requested r
  WHERE eval_log.trace_id = ANY(r.trace_ids)
  RETURNING eval_log.id
),
deleted_trace_annotations AS (
  DELETE FROM trace_annotation annotation
  USING requested r
  WHERE annotation.trace_id = ANY(r.trace_ids)
  RETURNING annotation.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = ANY(r.trace_ids)
  RETURNING trace.id
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version pv
  USING requested r
  WHERE pv.id = r.project_version_id
  RETURNING pv.id
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_eval_log_count', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_trace_annotation_count', (SELECT count(*) FROM deleted_trace_annotations),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_observation_span span, requested r
      WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace trace, requested r
      WHERE trace.id = ANY(r.trace_ids)
    )
  END,
  'remaining_project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_project_versions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project_version pv, requested r
      WHERE pv.id = r.project_version_id
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteTraceAnnotationArtifacts({
  scoreIds = [],
  spanId,
  traceId,
  labelId,
  userId,
  noteText,
}) {
  assert(
    String(spanId || "").trim(),
    "spanId must be set for trace annotation cleanup.",
  );
  assert(
    isUuid(traceId),
    "traceId must be a UUID for trace annotation cleanup.",
  );
  assert(
    isUuid(labelId),
    "labelId must be a UUID for trace annotation cleanup.",
  );
  assert(isUuid(userId), "userId must be a UUID for trace annotation cleanup.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuidArrayOrEmpty(scoreIds)} AS score_ids,
    ${sqlString(spanId)} AS span_id,
    ${sqlUuid(traceId)} AS trace_id,
    ${sqlUuid(labelId)} AS label_id,
    ${sqlUuid(userId)} AS user_id,
    ${sqlString(noteText)} AS note_text
),
deleted_scores AS (
  DELETE FROM model_hub_score score
  USING requested r
  WHERE score.id = ANY(r.score_ids)
     OR (
       score.observation_span_id = r.span_id
       AND score.label_id = r.label_id
       AND score.annotator_id = r.user_id
     )
  RETURNING score.id
),
deleted_notes AS (
  DELETE FROM tracer_spannotes note
  USING requested r
  WHERE note.span_id = r.span_id
    AND note.created_by_annotator = r.user_id::text
    AND note.notes = r.note_text
  RETURNING note.id
),
deleted_legacy_annotations AS (
  DELETE FROM trace_annotation annotation
  USING requested r
  WHERE annotation.annotation_label_id = r.label_id
    AND (
      annotation.trace_id = r.trace_id
      OR annotation.observation_span_id = r.span_id
    )
  RETURNING annotation.id
)
SELECT json_build_object(
  'deleted_score_count', (SELECT count(*) FROM deleted_scores),
  'deleted_note_count', (SELECT count(*) FROM deleted_notes),
  'deleted_legacy_trace_annotation_count', (SELECT count(*) FROM deleted_legacy_annotations),
  'remaining_score_count', (
    SELECT count(*)
    FROM model_hub_score score, requested r
    WHERE score.id = ANY(r.score_ids)
       OR (
         score.observation_span_id = r.span_id
         AND score.label_id = r.label_id
         AND score.annotator_id = r.user_id
       )
  ),
  'remaining_note_count', (
    SELECT count(*)
    FROM tracer_spannotes note, requested r
    WHERE note.span_id = r.span_id
      AND note.created_by_annotator = r.user_id::text
      AND note.notes = r.note_text
  )
);
`;
  return runPostgresJson(sql);
}

async function insertTraceSessionEvalLog({ sessionId, explanation }) {
  assert(isUuid(sessionId), "sessionId must be a UUID for eval-log seed.");
  const evalLogId = randomUUID();
  const sql = `
WITH inserted AS (
  INSERT INTO tracer_eval_logger (
    id,
    trace_id,
    observation_span_id,
    trace_session_id,
    target_type,
    output_bool,
    eval_explanation,
    error,
    output_str_list,
    results_tags,
    eval_tags,
    results_explanation,
    created_at,
    updated_at,
    deleted
  )
  VALUES (
    ${sqlUuid(evalLogId)},
    NULL,
    NULL,
    ${sqlUuid(sessionId)},
    'session',
    true,
    ${sqlString(explanation)},
    false,
    '[]'::jsonb,
    '[]'::jsonb,
    '[]'::jsonb,
    '{}'::jsonb,
    now(),
    now(),
    false
  )
  RETURNING id::text
)
SELECT json_build_object('eval_log_id', (SELECT id FROM inserted));
`;
  return runPostgresJson(sql);
}

async function hardDeleteTraceSessionLifecycleArtifacts({
  projectId,
  projectVersionId,
  sessionId,
  traceIds,
  spanIds,
  evalLogIds,
}) {
  assert(isUuid(projectId), "projectId must be a UUID for session cleanup.");
  assert(
    isUuid(projectVersionId),
    "projectVersionId must be a UUID for session cleanup.",
  );
  assert(isUuid(sessionId), "sessionId must be a UUID for session cleanup.");
  assert(
    asArray(traceIds).length > 0,
    "traceIds must be set for session cleanup.",
  );
  assert(
    asArray(spanIds).length > 0,
    "spanIds must be set for session cleanup.",
  );
  assert(
    asArray(evalLogIds).length > 0,
    "evalLogIds must be set for session cleanup.",
  );
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(projectId)} AS project_id,
    ${sqlUuid(projectVersionId)} AS project_version_id,
    ${sqlUuid(sessionId)} AS session_id,
    ${sqlUuidArray(traceIds)} AS trace_ids,
    ${sqlStringArray(spanIds)} AS span_ids,
    ${sqlUuidArray(evalLogIds)} AS eval_log_ids
),
deleted_eval_logs AS (
  DELETE FROM tracer_eval_logger eval_log
  USING requested r
  WHERE eval_log.id = ANY(r.eval_log_ids)
     OR eval_log.trace_session_id = r.session_id
     OR eval_log.trace_id = ANY(r.trace_ids)
  RETURNING eval_log.id
),
deleted_spans AS (
  DELETE FROM tracer_observation_span span
  USING requested r
  WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
  RETURNING span.id
),
deleted_traces AS (
  DELETE FROM tracer_trace trace
  USING requested r
  WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
  RETURNING trace.id
),
deleted_sessions AS (
  DELETE FROM trace_session session
  USING requested r
  WHERE session.id = r.session_id
  RETURNING session.id
),
deleted_project_versions AS (
  DELETE FROM tracer_project_version pv
  USING requested r
  WHERE pv.id = r.project_version_id
  RETURNING pv.id
),
deleted_projects AS (
  DELETE FROM tracer_project project
  USING requested r
  WHERE project.id = r.project_id
  RETURNING project.id
)
SELECT json_build_object(
  'deleted_eval_log_count', (SELECT count(*) FROM deleted_eval_logs),
  'deleted_span_count', (SELECT count(*) FROM deleted_spans),
  'deleted_trace_count', (SELECT count(*) FROM deleted_traces),
  'deleted_session_count', (SELECT count(*) FROM deleted_sessions),
  'deleted_project_version_count', (SELECT count(*) FROM deleted_project_versions),
  'deleted_project_count', (SELECT count(*) FROM deleted_projects),
  'remaining_eval_log_count', CASE
    WHEN (SELECT count(*) FROM deleted_eval_logs) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_eval_logger eval_log, requested r
      WHERE eval_log.id = ANY(r.eval_log_ids)
         OR eval_log.trace_session_id = r.session_id
         OR eval_log.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_span_count', CASE
    WHEN (SELECT count(*) FROM deleted_spans) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_observation_span span, requested r
      WHERE span.id = ANY(r.span_ids) OR span.trace_id = ANY(r.trace_ids)
    )
  END,
  'remaining_trace_count', CASE
    WHEN (SELECT count(*) FROM deleted_traces) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_trace trace, requested r
      WHERE trace.id = ANY(r.trace_ids) OR trace.session_id = r.session_id
    )
  END,
  'remaining_session_count', CASE
    WHEN (SELECT count(*) FROM deleted_sessions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM trace_session session, requested r
      WHERE session.id = r.session_id
    )
  END,
  'remaining_project_version_count', CASE
    WHEN (SELECT count(*) FROM deleted_project_versions) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project_version pv, requested r
      WHERE pv.id = r.project_version_id
    )
  END,
  'remaining_project_count', CASE
    WHEN (SELECT count(*) FROM deleted_projects) > 0 THEN 0
    ELSE (
      SELECT count(*) FROM tracer_project project, requested r
      WHERE project.id = r.project_id
    )
  END
);
`;
  return runPostgresJson(sql);
}

async function loadOtelProjectResolutionAudit({
  sourceProjectId,
  resolvedProjectId,
  organizationId,
  workspaceId,
}) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(sourceProjectId)} AS source_project_id,
    ${sqlUuid(resolvedProjectId)} AS resolved_project_id,
    ${sqlUuid(organizationId)} AS organization_id,
    ${sqlUuid(workspaceId)} AS workspace_id
),
source_project AS (
  SELECT project.id, project.name, project.trace_type, project.organization_id, project.workspace_id
  FROM tracer_project project, requested r
  WHERE project.id = r.source_project_id
),
resolved_project AS (
  SELECT project.id, project.name, project.trace_type, project.organization_id, project.workspace_id
  FROM tracer_project project, requested r
  WHERE project.id = r.resolved_project_id
)
SELECT json_build_object(
  'source_project_id', (SELECT id::text FROM source_project),
  'source_project_name', (SELECT name FROM source_project),
  'source_workspace_id', (SELECT workspace_id::text FROM source_project),
  'resolved_project_id', (SELECT id::text FROM resolved_project),
  'resolved_project_name', (SELECT name FROM resolved_project),
  'resolved_workspace_id', (SELECT workspace_id::text FROM resolved_project),
  'same_name', (
    SELECT source_project.name = resolved_project.name
    FROM source_project, resolved_project
  ),
  'same_type', (
    SELECT source_project.trace_type = resolved_project.trace_type
    FROM source_project, resolved_project
  ),
  'duplicate_project_name_count', COALESCE((
    SELECT count(*)
    FROM tracer_project project
    JOIN source_project source ON source.name = project.name
      AND source.trace_type = project.trace_type
    JOIN requested r ON project.organization_id = r.organization_id
    WHERE project.deleted = false
  ), 0)
);
`;
  return runPostgresJson(sql);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

async function runBackendShellJson(script) {
  const container = process.env.API_JOURNEY_BACKEND_CONTAINER || "ws2-backend";
  const python = process.env.API_JOURNEY_BACKEND_PYTHON || "python";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, python, "manage.py", "shell", "-c", script],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const lines = stdout
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const jsonLine = [...lines].reverse().find((line) => line.startsWith("{"));
  assert(jsonLine, "Backend shell returned no JSON output.");
  return JSON.parse(jsonLine);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlUuidArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL UUID array must not be empty.");
  return `ARRAY[${rows.map(sqlUuid).join(",")}]::uuid[]`;
}

function sqlUuidArrayOrEmpty(values) {
  const rows = asArray(values).filter(Boolean);
  if (!rows.length) return "ARRAY[]::uuid[]";
  return `ARRAY[${rows.map(sqlUuid).join(",")}]::uuid[]`;
}

function sqlStringArray(values) {
  const rows = asArray(values);
  assert(rows.length > 0, "SQL text array must not be empty.");
  return `ARRAY[${rows.map(sqlString).join(",")}]::text[]`;
}

function sqlNumber(value) {
  const number = Number(value);
  assert(Number.isFinite(number), "SQL numeric value must be finite.");
  return String(number);
}

function traceWritePayload({
  id,
  projectId,
  projectVersionId,
  sessionId,
  name,
  runId,
  marker,
  metadata = { source: "api-journey", run_id: runId, trace_marker: marker },
}) {
  return {
    ...(id ? { id } : {}),
    project: projectId,
    project_version: projectVersionId,
    ...(sessionId ? { session: sessionId } : {}),
    name,
    metadata,
    input: {
      prompt: `api journey trace ${marker}`,
      shared: `api journey trace shared ${runId}`,
    },
    output: { response: `api journey trace response ${marker}` },
    error: null,
    tags: ["api-journey", `trace-${marker}`],
  };
}

function traceSessionWritePayload({ projectId, name, bookmarked }) {
  return {
    project: projectId,
    name,
    bookmarked,
  };
}

function sqlTraceTaskStatus(value) {
  assert(
    ["running", "waiting", "paused"].includes(value),
    "SQL trace error task status value was invalid.",
  );
  return `'${value}'`;
}

function sqlEvalTaskStatus(value) {
  assert(
    ["pending", "running", "completed", "failed", "paused", "deleted"].includes(
      value,
    ),
    "SQL eval task status value was invalid.",
  );
  return sqlString(value);
}

function sqlTimestampOrNull(value) {
  if (!value) {
    return "NULL";
  }
  return `${sqlString(value)}::timestamptz`;
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlStringOrNull(value) {
  if (value === null || value === undefined || value === "") return "NULL";
  return sqlString(value);
}

function sqlUuidOrNull(value) {
  if (!value) return "NULL::uuid";
  return sqlUuid(value);
}

async function expectApiJourneyHttpStatus(status, fn, label) {
  try {
    await fn();
  } catch (error) {
    if (error instanceof ApiJourneyError && error.status === status) {
      return error;
    }
    throw error;
  }
  throw new Error(`${label} should have failed with HTTP ${status}.`);
}

function responseCount(payload) {
  if (typeof payload?.metadata?.total_rows === "number") {
    return payload.metadata.total_rows;
  }
  if (typeof payload?.total_count === "number") return payload.total_count;
  if (typeof payload?.total === "number") return payload.total;
  if (typeof payload?.count === "number") return payload.count;
  return asArray(payload).length;
}

function dashboardQueryConfig(projectId) {
  assert(
    isUuid(projectId),
    "projectId must be a UUID for dashboard query config.",
  );
  return {
    workflow: "observability",
    project_ids: [projectId],
    time_range: { preset: "7D" },
    granularity: "day",
    metrics: [
      {
        id: "latency",
        name: "latency",
        display_name: "Latency",
        type: "system_metric",
        source: "traces",
        aggregation: "avg",
        unit: "ms",
      },
    ],
    filters: [],
    breakdowns: [],
  };
}

function assertDashboardPayload(
  payload,
  { dashboardId, name, description, workspaceId },
) {
  assert(payload?.id === dashboardId, "Dashboard payload returned wrong id.");
  assert(payload?.name === name, "Dashboard payload returned wrong name.");
  assert(
    payload?.description === description,
    "Dashboard payload returned wrong description.",
  );
  assert(
    payload?.workspace === workspaceId,
    "Dashboard payload returned wrong workspace.",
  );
  assert(
    Array.isArray(payload?.widgets),
    "Dashboard detail/create payload must include widgets array.",
  );
}

function assertWidgetPayload(payload, { widgetId, name, width, chartType }) {
  assert(isUuid(widgetId), "Expected widget id must be a UUID.");
  assert(payload?.id === widgetId, "Widget payload returned wrong id.");
  assert(payload?.name === name, "Widget payload returned wrong name.");
  assert(payload?.width === width, "Widget payload returned wrong width.");
  assert(
    payload?.chart_config?.chart_type === chartType,
    "Widget payload returned wrong chart type.",
  );
}

function assertDashboardDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    dashboardId,
    widgetIds,
    expectedDashboardDeleted,
    expectedActiveWidgetCount,
  },
) {
  assert(
    audit?.dashboard_id === dashboardId,
    "Dashboard DB audit returned wrong id.",
  );
  assert(
    audit?.dashboard_organization_id === organizationId,
    "Dashboard DB audit returned wrong organization.",
  );
  assert(
    audit?.dashboard_workspace_id === workspaceId,
    "Dashboard DB audit returned wrong workspace.",
  );
  assert(
    audit?.dashboard_deleted === expectedDashboardDeleted,
    "Dashboard DB audit deleted flag mismatch.",
  );
  assert(
    Boolean(audit?.dashboard_deleted_at_set) === expectedDashboardDeleted,
    "Dashboard DB audit deleted_at flag mismatch.",
  );
  assert(
    Number(audit?.active_widget_count) === expectedActiveWidgetCount,
    "Dashboard DB audit active widget count mismatch.",
  );
  const rows = asArray(audit?.widgets);
  for (const widgetId of widgetIds) {
    const row = rows.find((widget) => widget.id === widgetId);
    assert(row, `Dashboard DB audit missing widget ${widgetId}.`);
    assert(
      row.dashboard_id === dashboardId,
      `Dashboard DB audit widget ${widgetId} had wrong dashboard id.`,
    );
  }
  if (expectedDashboardDeleted) {
    assert(
      rows.every((widget) => widget.deleted && widget.deleted_at_set),
      "Dashboard delete should soft-delete every audited widget.",
    );
  }
}

function observationSpanPayload(payload) {
  return payload?.observation_span || payload;
}

function observationSpanWritePayload({
  id,
  projectId,
  projectVersionId,
  traceId,
  parentSpanId,
  observationType = "llm",
  name,
  runId,
  startTime = new Date(Date.now() - 1000).toISOString(),
  endTime = new Date().toISOString(),
  metadata = { source: "api-journey", run_id: runId },
  statusMessage = "api journey span mutation",
}) {
  return {
    ...(id ? { id } : {}),
    project: projectId,
    project_version: projectVersionId,
    trace: traceId,
    ...(parentSpanId ? { parent_span_id: parentSpanId } : {}),
    name,
    observation_type: observationType,
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: "api journey input" }] },
    output: { choices: [{ message: { content: "api journey output" } }] },
    model: "api-journey-model",
    prompt_tokens: 2,
    completion_tokens: 3,
    total_tokens: 5,
    latency_ms: 123,
    cost: 0,
    status: "OK",
    status_message: statusMessage,
    tags: ["api-journey"],
    metadata,
  };
}

function assertObservationSpanDetail(
  payload,
  { spanId, traceId, projectId, projectVersionId },
) {
  assert(payload?.id === spanId, "Observation span detail returned wrong id.");
  assert(
    payload?.trace === traceId,
    "Observation span detail returned wrong trace id.",
  );
  assert(
    payload?.project === projectId,
    "Observation span detail returned wrong project id.",
  );
  assert(
    payload?.project_version === projectVersionId,
    "Observation span detail returned wrong project version id.",
  );
  assert(
    String(payload?.name || "").trim(),
    "Observation span detail omitted span name.",
  );
}

function assertTraceLifecycleDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    projectVersionId,
    activeTraceIds,
    deletedTraceIds,
    activeSpanIds,
    deletedSpanIds,
    expectedTag,
  },
) {
  const traces = asArray(audit?.traces);
  const spans = asArray(audit?.spans);
  const projectVersion = audit?.project_version || {};
  assert(
    projectVersion.id === projectVersionId &&
      projectVersion.project_id === projectId,
    "Trace DB audit project-version row did not match the disposable project/run.",
  );

  for (const traceId of [
    ...asArray(activeTraceIds),
    ...asArray(deletedTraceIds),
  ]) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row, `Trace DB audit did not include trace ${traceId}.`);
    assert(
      row.organization_id === organizationId,
      "Trace DB audit organization mismatch.",
    );
    assert(
      audit.workspace_is_default || row.workspace_id === workspaceId,
      "Trace DB audit workspace mismatch.",
    );
    assert(row.project_id === projectId, "Trace DB audit project mismatch.");
    assert(
      row.project_version_id === projectVersionId,
      "Trace DB audit project-version mismatch.",
    );
  }

  for (const traceId of asArray(activeTraceIds)) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row.deleted === false, `Trace ${traceId} should still be active.`);
  }
  for (const traceId of asArray(deletedTraceIds)) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row.deleted === true, `Trace ${traceId} should be soft-deleted.`);
    assert(
      row.deleted_at_set === true,
      `Trace ${traceId} should have deleted_at set.`,
    );
  }

  const taggedTrace = traces.find((row) =>
    asArray(row.tags).includes(expectedTag),
  );
  assert(taggedTrace, "Trace DB audit did not persist the updated tag.");

  for (const spanId of [
    ...asArray(activeSpanIds),
    ...asArray(deletedSpanIds),
  ]) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row, `Trace DB audit did not include span ${spanId}.`);
    assert(
      row.organization_id === organizationId,
      "Trace span DB audit organization mismatch.",
    );
    assert(
      audit.workspace_is_default || row.workspace_id === workspaceId,
      "Trace span DB audit workspace mismatch.",
    );
    assert(
      row.project_id === projectId,
      "Trace span DB audit project mismatch.",
    );
    assert(
      row.project_version_id === projectVersionId,
      "Trace span DB audit project-version mismatch.",
    );
  }
  for (const spanId of asArray(activeSpanIds)) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row.deleted === false, `Span ${spanId} should still be active.`);
  }
  for (const spanId of asArray(deletedSpanIds)) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row.deleted === true, `Span ${spanId} should be soft-deleted.`);
    assert(
      row.deleted_at_set === true,
      `Span ${spanId} should have deleted_at set.`,
    );
  }
}

function assertTraceSessionLifecycleDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    projectVersionId,
    sessionId,
    activeTraceIds,
    deletedTraceIds,
    activeSpanIds,
    deletedSpanIds,
    activeEvalLogIds,
    deletedEvalLogIds,
  },
) {
  const session = audit?.session || {};
  const projectVersion = audit?.project_version || {};
  const traces = asArray(audit?.traces);
  const spans = asArray(audit?.spans);
  const evalLogs = asArray(audit?.eval_logs);

  assert(
    projectVersion.id === projectVersionId &&
      projectVersion.project_id === projectId,
    "Trace-session DB audit project-version row did not match the disposable project/run.",
  );
  assert(
    session.id === sessionId,
    "Trace-session DB audit returned wrong session id.",
  );
  assert(
    session.project_id === projectId,
    "Trace-session DB audit returned wrong project id.",
  );
  assert(
    session.organization_id === organizationId,
    "Trace-session DB audit returned wrong organization.",
  );
  assert(
    audit.workspace_is_default || session.workspace_id === workspaceId,
    "Trace-session DB audit returned wrong workspace.",
  );

  for (const traceId of [
    ...asArray(activeTraceIds),
    ...asArray(deletedTraceIds),
  ]) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row, `Trace-session DB audit did not include trace ${traceId}.`);
    assert(
      row.session_id === sessionId,
      "Trace-session DB audit trace session mismatch.",
    );
    assert(
      row.project_id === projectId,
      "Trace-session DB audit trace project mismatch.",
    );
    assert(
      row.project_version_id === projectVersionId,
      "Trace-session DB audit trace run mismatch.",
    );
  }
  for (const traceId of asArray(activeTraceIds)) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row.deleted === false, `Trace ${traceId} should still be active.`);
  }
  for (const traceId of asArray(deletedTraceIds)) {
    const row = traces.find((candidate) => candidate.id === traceId);
    assert(row.deleted === true, `Trace ${traceId} should be soft-deleted.`);
    assert(
      row.deleted_at_set === true,
      `Trace ${traceId} should have deleted_at set.`,
    );
  }

  for (const spanId of [
    ...asArray(activeSpanIds),
    ...asArray(deletedSpanIds),
  ]) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row, `Trace-session DB audit did not include span ${spanId}.`);
    assert(
      row.project_id === projectId,
      "Trace-session DB audit span project mismatch.",
    );
    assert(
      row.project_version_id === projectVersionId,
      "Trace-session DB audit span run mismatch.",
    );
  }
  for (const spanId of asArray(activeSpanIds)) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row.deleted === false, `Span ${spanId} should still be active.`);
  }
  for (const spanId of asArray(deletedSpanIds)) {
    const row = spans.find((candidate) => candidate.id === spanId);
    assert(row.deleted === true, `Span ${spanId} should be soft-deleted.`);
    assert(
      row.deleted_at_set === true,
      `Span ${spanId} should have deleted_at set.`,
    );
  }

  for (const evalLogId of [
    ...asArray(activeEvalLogIds),
    ...asArray(deletedEvalLogIds),
  ]) {
    const row = evalLogs.find((candidate) => candidate.id === evalLogId);
    assert(
      row,
      `Trace-session DB audit did not include eval log ${evalLogId}.`,
    );
    assert(
      row.trace_session_id === sessionId,
      "Trace-session DB audit eval log session mismatch.",
    );
    assert(
      row.target_type === "session",
      "Trace-session eval log target type mismatch.",
    );
  }
  for (const evalLogId of asArray(activeEvalLogIds)) {
    const row = evalLogs.find((candidate) => candidate.id === evalLogId);
    assert(
      row.deleted === false,
      `Eval log ${evalLogId} should still be active.`,
    );
  }
  for (const evalLogId of asArray(deletedEvalLogIds)) {
    const row = evalLogs.find((candidate) => candidate.id === evalLogId);
    assert(
      row.deleted === true,
      `Eval log ${evalLogId} should be soft-deleted.`,
    );
    assert(
      row.deleted_at_set === true,
      `Eval log ${evalLogId} should have deleted_at set.`,
    );
  }
}

function assertSpanIndexPayload(payload, label) {
  assert(
    Object.prototype.hasOwnProperty.call(payload || {}, "next_trace_id"),
    `${label} response omitted next_trace_id.`,
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload || {}, "previous_trace_id"),
    `${label} response omitted previous_trace_id.`,
  );
  for (const key of ["next_trace_id", "previous_trace_id"]) {
    if (payload[key] !== null) {
      assert(isUuid(payload[key]), `${label} ${key} must be null or a UUID.`);
    }
  }
}

function assertObservationSpanDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    spanId,
    traceId,
    rootSpanId,
    expectedTag,
  },
) {
  const row = audit?.selected_span || {};
  assert(
    row.id === spanId,
    "Observation span DB audit returned wrong span id.",
  );
  assert(
    row.trace_id === traceId,
    "Observation span DB audit returned wrong trace id.",
  );
  assert(
    row.project_id === projectId,
    "Observation span DB audit returned wrong project id.",
  );
  assert(
    row.organization_id === organizationId,
    "Observation span DB audit returned wrong organization id.",
  );
  assert(
    row.workspace_id === workspaceId ||
      (audit.workspace_is_default === true && row.workspace_id === null),
    "Observation span DB audit returned a span outside the requested workspace scope.",
  );
  assert(
    Number(audit.visible_span_count) > 0,
    "Observation span DB audit found no visible spans for the project.",
  );
  assert(
    audit.root_span_id === rootSpanId,
    "Observation span DB audit root span id did not match API root-spans response.",
  );
  assert(
    normalizeTags(audit.selected_tags).includes(expectedTag),
    "Observation span DB audit did not read back the temporary tag.",
  );
}

function assertObservationSpanMutationDbAudit(
  audit,
  {
    organizationId,
    workspaceId,
    projectId,
    spanIds,
    updatedSpanId,
    updatedSpanName,
    expectedProjectIdsBySpanId = {},
    labelId,
  },
) {
  const rows = asArray(audit?.spans);
  const rowsById = new Map(rows.map((row) => [row.id, row]));
  for (const spanId of spanIds) {
    const row = rowsById.get(spanId);
    assert(row, `Observation span mutation DB audit missed span ${spanId}.`);
    assert(
      row.deleted === false,
      `Observation span ${spanId} was unexpectedly deleted.`,
    );
    const expectedProjectId = expectedProjectIdsBySpanId[spanId] || projectId;
    assert(
      row.project_id === expectedProjectId,
      `Observation span ${spanId} DB audit returned wrong project id.`,
    );
    assert(
      row.organization_id === organizationId,
      `Observation span ${spanId} DB audit returned wrong organization id.`,
    );
    assert(
      row.workspace_id === workspaceId ||
        (audit.workspace_is_default === true && row.workspace_id === null),
      `Observation span ${spanId} DB audit returned a row outside the workspace scope.`,
    );
  }

  const updated = rowsById.get(updatedSpanId);
  assert(
    updated?.name === updatedSpanName,
    "Observation span mutation DB audit did not read back the PUT name.",
  );
  assert(
    updated?.metadata?.put === true,
    "Observation span mutation DB audit did not read back the PUT metadata.",
  );

  const label = audit?.label || {};
  assert(
    label.id === labelId,
    "Observation span label-delete audit returned wrong label id.",
  );
  assert(
    label.deleted === true,
    "Observation span label-delete audit found an active label.",
  );
  assert(
    label.deleted_at_set === true,
    "Observation span label-delete audit did not find deleted_at populated.",
  );
  assert(
    label.organization_id === organizationId,
    "Observation span label-delete audit returned wrong organization id.",
  );
  assert(
    label.workspace_id === workspaceId ||
      (audit.workspace_is_default === true && label.workspace_id === null),
    "Observation span label-delete audit returned a label outside the workspace scope.",
  );
}

async function findAlertByName(client, name) {
  const list = await client.get(apiPath("/tracer/user-alerts/list_monitors/"), {
    query: {
      page_number: 0,
      page_size: 25,
      search_text: name,
      sort_by: "created_at",
      sort_direction: "desc",
    },
  });
  return asArray(list.table || list).find((row) => row.name === name);
}

async function loadAlertListDbAudit({ organizationId, workspaceId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(organizationId)} AS organization_id, ${sqlUuid(workspaceId)} AS workspace_id
),
workspace_row AS (
  SELECT workspace.id, workspace.organization_id, workspace.is_default
  FROM accounts_workspace workspace
  JOIN requested r ON workspace.id = r.workspace_id
  WHERE workspace.organization_id = r.organization_id
),
monitor_counts AS (
  SELECT
    count(*) FILTER (
      WHERE monitor.deleted = false
        AND monitor.organization_id = r.organization_id
    ) AS org_monitor_count,
    count(*) FILTER (
      WHERE monitor.deleted = false
        AND monitor.organization_id = r.organization_id
        AND monitor.workspace_id = r.workspace_id
    ) AS workspace_monitor_count,
    count(*) FILTER (
      WHERE monitor.deleted = false
        AND monitor.organization_id = r.organization_id
        AND monitor.workspace_id IS NULL
    ) AS null_workspace_monitor_count,
    count(*) FILTER (
      WHERE monitor.deleted = false
        AND monitor.organization_id = r.organization_id
        AND monitor.workspace_id IS NOT NULL
        AND monitor.workspace_id <> r.workspace_id
    ) AS other_workspace_monitor_count,
    count(*) FILTER (
      WHERE monitor.deleted = false
        AND monitor.organization_id = r.organization_id
        AND (
          (workspace.is_default = true AND (
            monitor.workspace_id = r.workspace_id
            OR monitor.workspace_id IS NULL
            OR monitor.workspace_id IN (
              SELECT default_workspace.id
              FROM accounts_workspace default_workspace
              WHERE default_workspace.organization_id = r.organization_id
                AND default_workspace.is_default = true
            )
          ))
          OR (workspace.is_default = false AND monitor.workspace_id = r.workspace_id)
        )
    ) AS visible_monitor_count
  FROM tracer_useralertmonitor monitor
  CROSS JOIN requested r
  CROSS JOIN workspace_row workspace
)
SELECT json_build_object(
  'workspace_is_default', (SELECT is_default FROM workspace_row),
  'org_monitor_count', org_monitor_count,
  'workspace_monitor_count', workspace_monitor_count,
  'null_workspace_monitor_count', null_workspace_monitor_count,
  'other_workspace_monitor_count', other_workspace_monitor_count,
  'visible_monitor_count', visible_monitor_count
)
FROM monitor_counts;
`;
  return runPostgresJson(sql);
}

async function loadAlertLifecycleAudit({ alertId }) {
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(alertId)} AS alert_id
),
alert_row AS (
  SELECT *
  FROM tracer_useralertmonitor monitor
  JOIN requested r ON monitor.id = r.alert_id
)
SELECT json_build_object(
  'alert_exists', EXISTS (SELECT 1 FROM alert_row),
  'alert_deleted', COALESCE((SELECT deleted FROM alert_row), false),
  'workspace_id', (SELECT workspace_id FROM alert_row),
  'project_id', (SELECT project_id FROM alert_row),
  'name', (SELECT name FROM alert_row),
  'is_mute', COALESCE((SELECT is_mute FROM alert_row), false),
  'critical_threshold_value', (SELECT critical_threshold_value FROM alert_row),
  'warning_threshold_value', (SELECT warning_threshold_value FROM alert_row),
  'log_count', (
    SELECT count(*)
    FROM tracer_useralertmonitorlog log
    JOIN requested r ON log.alert_id = r.alert_id
  ),
  'resolved_log_count', (
    SELECT count(*)
    FROM tracer_useralertmonitorlog log
    JOIN requested r ON log.alert_id = r.alert_id
    WHERE log.resolved = true
  ),
  'unresolved_log_count', (
    SELECT count(*)
    FROM tracer_useralertmonitorlog log
    JOIN requested r ON log.alert_id = r.alert_id
    WHERE log.resolved = false
  )
);
`;
  return runPostgresJson(sql);
}

async function loadAlertNotificationAudit({ alertId, logId }) {
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(alertId)} AS alert_id,
    ${sqlUuidOrNull(logId)} AS log_id
),
alert_row AS (
  SELECT *
  FROM tracer_useralertmonitor monitor
  JOIN requested r ON monitor.id = r.alert_id
),
log_row AS (
  SELECT *
  FROM tracer_useralertmonitorlog log
  JOIN requested r ON log.alert_id = r.alert_id
  WHERE r.log_id IS NULL OR log.id = r.log_id
  ORDER BY log.created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'alert_exists', EXISTS (SELECT 1 FROM alert_row),
  'alert_deleted', COALESCE((SELECT deleted FROM alert_row), false),
  'workspace_id', (SELECT workspace_id FROM alert_row),
  'project_id', (SELECT project_id FROM alert_row),
  'notification_email_count', COALESCE((
    SELECT cardinality(notification_emails) FROM alert_row
  ), 0),
  'slack_webhook_url', (SELECT slack_webhook_url FROM alert_row),
  'log_exists', EXISTS (SELECT 1 FROM log_row),
  'log_id', (SELECT id FROM log_row),
  'log_type', (SELECT type FROM log_row),
  'log_resolved', COALESCE((SELECT resolved FROM log_row), false),
  'time_window_start_set', COALESCE((
    SELECT time_window_start IS NOT NULL FROM log_row
  ), false),
  'time_window_end_set', COALESCE((
    SELECT time_window_end IS NOT NULL FROM log_row
  ), false)
);
`;
  return runPostgresJson(sql);
}

async function triggerAlertThresholdInBackend({ alertId, currentValue }) {
  assert(isUuid(alertId), "alertId must be a UUID for alert trigger.");
  const script = `
import json
import os
import sys
import types
from datetime import timedelta
from django.utils import timezone
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog

tasks_package = types.ModuleType("model_hub.tasks")
tasks_package.__path__ = [os.path.join(os.getcwd(), "model_hub", "tasks")]
sys.modules.setdefault("model_hub.tasks", tasks_package)

from tracer.utils.monitor import _check_thresholds_and_alert

monitor = UserAlertMonitor.objects.get(id="${alertId}")
before = UserAlertMonitorLog.objects.filter(alert=monitor).count()
now = timezone.now()
time_window_start = now - timedelta(minutes=monitor.alert_frequency)
_check_thresholds_and_alert(monitor, ${sqlNumber(currentValue)}, time_window_start, now)
after = UserAlertMonitorLog.objects.filter(alert=monitor).count()
latest = (
    UserAlertMonitorLog.objects
    .filter(alert=monitor)
    .order_by("-created_at")
    .values("id", "type", "message", "resolved", "time_window_start", "time_window_end")
    .first()
)
if latest:
    latest["id"] = str(latest["id"])
    latest["time_window_start"] = latest["time_window_start"].isoformat() if latest["time_window_start"] else None
    latest["time_window_end"] = latest["time_window_end"].isoformat() if latest["time_window_end"] else None
print(json.dumps({
    "alert_id": str(monitor.id),
    "before_count": before,
    "after_count": after,
    "current_value": ${sqlNumber(currentValue)},
    "latest_log": latest,
}))
`;
  return runBackendShellJson(script);
}

async function insertAlertLog({ alertId, logId, message }) {
  const sql = `
WITH inserted AS (
  INSERT INTO tracer_useralertmonitorlog (
    id,
    alert_id,
    type,
    message,
    resolved,
    created_at,
    updated_at,
    deleted
  )
  VALUES (
    ${sqlUuid(logId)},
    ${sqlUuid(alertId)},
    'critical',
    ${sqlString(message)},
    false,
    NOW(),
    NOW(),
    false
  )
  RETURNING id
)
SELECT json_build_object(
  'inserted_log_id', (SELECT id FROM inserted)
);
`;
  return runPostgresJson(sql);
}

async function deleteAlertDbArtifacts({ alertIds }) {
  const sql = `
WITH requested AS (
  SELECT unnest(${sqlUuidArray(alertIds)}) AS alert_id
),
deleted_logs AS (
  DELETE FROM tracer_useralertmonitorlog
  WHERE alert_id IN (SELECT alert_id FROM requested)
  RETURNING id
),
deleted_alerts AS (
  DELETE FROM tracer_useralertmonitor
  WHERE id IN (SELECT alert_id FROM requested)
  RETURNING id
)
SELECT json_build_object(
  'deleted_log_rows', (SELECT count(*) FROM deleted_logs),
  'deleted_alert_rows', (SELECT count(*) FROM deleted_alerts)
);
`;
  return runPostgresJson(sql);
}

async function resolveObserveFilterCoverage(client, evidence) {
  const preferredProjectId =
    process.env.OBSERVE_FILTERS_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe filters project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 100 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const metricsPayload = await client.get(
      apiPath("/tracer/dashboard/metrics/"),
      {
        query: { project_ids: project.id },
      },
    );
    const metrics = asArray(metricsPayload.metrics || metricsPayload);
    if (!metrics.length) continue;

    const statusValues = asArray(
      (
        await client.get(apiPath("/tracer/dashboard/filter_values/"), {
          query: {
            metric_name: "status",
            metric_type: "system_metric",
            project_ids: project.id,
            source: "traces",
          },
        })
      ).values,
    );
    if (!statusValues.length) continue;

    const customAttribute = await firstMetricWithValues(
      client,
      project.id,
      metrics,
      {
        category: "custom_attribute",
        metricType: "custom_attribute",
        preferName: "fi.trace.source",
      },
    );
    if (!customAttribute) continue;

    const annotationMetric = await firstMetricWithValues(
      client,
      project.id,
      metrics,
      {
        category: "annotation_metric",
        metricType: "annotation_metric",
      },
    );
    if (!annotationMetric) continue;

    const evalMetric = metrics.find(
      (metric) =>
        metric?.category === "eval_metric" &&
        (asArray(metric.choices).length > 0 || metric.output_type),
    );
    if (!evalMetric?.name) continue;

    const sessionValues = asArray(
      (
        await client.get(
          apiPath("/tracer/trace-session/get_session_filter_values/"),
          {
            query: {
              project_id: project.id,
              column: "session_id",
              page: 0,
              page_size: 10,
            },
          },
        )
      ).values,
    );
    if (!sessionValues.length) continue;

    const userValues = asArray(
      (
        await client.get(
          apiPath("/tracer/trace-session/get_session_filter_values/"),
          {
            query: {
              project_id: project.id,
              column: "user_id",
              page: 0,
              page_size: 10,
            },
          },
        )
      ).values,
    );
    if (!userValues.length) continue;

    evidence.push({
      endpoint: "observe filter coverage project search",
      project_id: project.id,
      custom_attribute: customAttribute.name,
      annotation_metric: metricLabel(annotationMetric),
      eval_metric: metricLabel(evalMetric),
    });
    return {
      project,
      metrics,
      statusValues,
      customAttribute,
      customAttributeValues: customAttribute.values,
      annotationMetric,
      annotationValues: annotationMetric.values,
      evalMetric,
      sessionValues,
      userValues,
    };
  }

  skip(
    "No observe project with system, custom attribute, annotation, eval, and session filter values is available.",
  );
}

async function firstMetricWithValues(
  client,
  projectId,
  metrics,
  { category, metricType, preferName } = {},
) {
  const candidates = metrics.filter((metric) => metric?.category === category);
  const ordered = [
    ...candidates.filter((metric) => metric.name === preferName),
    ...candidates.filter((metric) => metric.name !== preferName),
  ];

  for (const metric of ordered.slice(0, 25)) {
    if (!metric?.name) continue;
    const values = asArray(
      (
        await client.get(apiPath("/tracer/dashboard/filter_values/"), {
          query: {
            metric_name: metric.name,
            metric_type: metricType,
            project_ids: projectId,
            source: "traces",
          },
        })
      ).values,
    );
    if (values.length > 0 || asArray(metric.choices).length > 0) {
      return { ...metric, values };
    }
  }

  return null;
}

async function resolveObserveVoiceFilterSample(client, evidence) {
  const preferredProjectId =
    process.env.OBSERVE_VOICE_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe voice project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 100 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const list = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_voice_calls/"), [], {
        project_id: project.id,
        page: 1,
        page_size: 5,
      }),
    );
    const calls = asArray(list).filter((row) => row?.trace_id);
    const call =
      calls.find((row) => row.turn_count !== undefined && row.ended_reason) ||
      calls.find((row) => row.turn_count !== undefined) ||
      calls[0];
    if (!call?.trace_id) continue;

    evidence.push({
      endpoint: "observe voice filter sample search",
      project_id: project.id,
      trace_id: call.trace_id,
      base_count: responseCount(list),
    });
    return {
      project,
      call,
      baseCount: responseCount(list),
    };
  }

  skip("No observe voice call is available for voice filter coverage.");
}

function assertNoStringifiedPaths(paths, label) {
  const bad = asArray(paths).filter((path) => /[{}]/.test(String(path)));
  assert(
    bad.length === 0,
    `${label} contains stringified object paths: ${bad.slice(0, 3).join(", ")}`,
  );
}

function assertNoNormalizedDuplicateKeys(keys, label) {
  const seen = new Map();
  const duplicates = [];
  for (const rawKey of asArray(keys)) {
    const key = String(rawKey);
    const normalized = normalizeAttributeKeyAlias(key);
    const previous = seen.get(normalized);
    if (previous && previous !== key) {
      duplicates.push(`${previous} / ${key}`);
    } else {
      seen.set(normalized, key);
    }
  }
  assert(
    duplicates.length === 0,
    `${label} contains camel/snake duplicate aliases: ${duplicates
      .slice(0, 3)
      .join(", ")}`,
  );
}

function normalizeAttributeKeyAlias(key) {
  return String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase();
}

function valueOfOption(option) {
  if (option && typeof option === "object") {
    return String(option.value ?? option.label ?? option.name ?? "");
  }
  return String(option ?? "");
}

function metricLabel(metric) {
  return String(
    metric?.display_name || metric?.displayName || metric?.name || "",
  );
}

function normalizeTags(value) {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      return value ? [value] : [];
    }
  }
  return [];
}

function uniqueTags(tags) {
  return [...new Set(tags.filter(Boolean).map(String))];
}

function arraysEqual(left, right) {
  return JSON.stringify(uniqueTags(left)) === JSON.stringify(uniqueTags(right));
}

async function findSessionNotAlreadyQueued(client, queueId, sessionRows) {
  for (const row of sessionRows) {
    if (!row?.session_id) continue;
    const queueEntry = await findQueueEntryForSource(
      client,
      queueId,
      row.session_id,
    );
    if (!queueEntry?.item?.id) return row;
  }
  return null;
}

async function findQueueEntryForSource(client, queueId, sessionId) {
  const entries = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: {
        source_type: "trace_session",
        source_id: sessionId,
      },
    }),
  );
  return entries.find((entry) => String(entry?.queue?.id) === String(queueId));
}

async function resolveObserveSpanForLifecycle(
  client,
  cleanup,
  runId,
  evidence,
) {
  const preferredProjectId = process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { project_type: "observe", page_number: 0, page_size: 50 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const observeList = await client.get(
      queryWithFilters(
        apiPath("/tracer/observation-span/list_spans_observe/"),
        [],
        {
          project_id: project.id,
          page_number: 0,
          page_size: 25,
        },
      ),
    );
    const rows = asArray(observeList).filter(
      (row) => row?.span_id && row?.trace_id,
    );
    for (const row of rows.slice(0, 10)) {
      try {
        const detailPayload = await client.get(
          apiPath("/tracer/observation-span/{id}/", { id: row.span_id }),
        );
        const detail = observationSpanPayload(detailPayload);
        if (
          detail?.project === project.id &&
          isUuid(detail?.trace) &&
          isUuid(detail?.project_version)
        ) {
          evidence.push({
            endpoint: "observe span lifecycle resolver",
            project_id: project.id,
            span_id: row.span_id,
            trace_id: row.trace_id,
            project_version_id: detail.project_version,
            observe_span_total_rows: observeList?.metadata?.total_rows ?? null,
          });
          return { project, span: row, detail, observeList };
        }
      } catch (error) {
        if (!(error instanceof ApiJourneyError && error.status === 400)) {
          throw error;
        }
      }
    }
  }

  return createDisposableObserveSpanForLifecycle(
    client,
    cleanup,
    runId,
    projects,
    evidence,
  );
}

async function createDisposableObserveSpanForLifecycle(
  client,
  cleanup,
  runId,
  projects,
  evidence,
) {
  const project = projects.find((row) => row?.id);
  if (!project?.id) {
    skip("No observe project exists for disposable span lifecycle coverage.");
  }

  const suffix = journeySafeId(runId);
  const projectVersion = await client.post(
    apiPath("/tracer/project-version/"),
    {
      project: project.id,
      name: `api journey span run ${suffix}`,
      metadata: { source: "api-journey", run_id: runId },
    },
  );
  const projectVersionId =
    projectVersion.project_version_id || projectVersion.id;
  assert(
    isUuid(projectVersionId),
    "Disposable project version create returned no id.",
  );
  cleanup.defer("delete OBS-API-013 project version", () =>
    client.delete(
      apiPath("/tracer/project-version/{id}/", { id: projectVersionId }),
      { okStatuses: [200, 204, 400, 404] },
    ),
  );

  const trace = await client.post(apiPath("/tracer/trace/"), {
    project: project.id,
    project_version: projectVersionId,
    name: `api journey span trace ${suffix}`,
    input: { prompt: "api journey observe span input" },
    output: { response: "api journey observe span output" },
    metadata: { source: "api-journey", run_id: runId },
    tags: ["api-journey"],
  });
  const traceId = trace.id || trace.trace_id || trace.trace?.id;
  assert(isUuid(traceId), "Disposable trace create returned no id.");
  cleanup.defer("delete OBS-API-013 trace", () =>
    client.delete(apiPath("/tracer/trace/{id}/", { id: traceId }), {
      okStatuses: [200, 204, 400, 404],
    }),
  );

  const spanId = `api_journey_span_${suffix}`;
  const startTime = new Date(Date.now() - 1000).toISOString();
  const endTime = new Date().toISOString();
  const span = await client.post(apiPath("/tracer/observation-span/"), {
    id: spanId,
    project: project.id,
    project_version: projectVersionId,
    trace: traceId,
    name: `api journey span ${suffix}`,
    observation_type: "llm",
    start_time: startTime,
    end_time: endTime,
    input: { messages: [{ role: "user", content: "hello" }] },
    output: { choices: [{ message: { content: "hi" } }] },
    model: "api-journey-model",
    prompt_tokens: 1,
    completion_tokens: 1,
    total_tokens: 2,
    latency_ms: 100,
    cost: 0,
    status: "OK",
    tags: [],
    metadata: { source: "api-journey", run_id: runId },
  });
  const createdSpanId = span.id || spanId;
  assert(
    createdSpanId === spanId,
    "Disposable observation span create returned wrong id.",
  );
  cleanup.defer("delete OBS-API-013 span", () =>
    client.delete(apiPath("/tracer/observation-span/{id}/", { id: spanId }), {
      okStatuses: [200, 204, 400, 404],
    }),
  );

  const observeList = await client.get(
    queryWithFilters(
      apiPath("/tracer/observation-span/list_spans_observe/"),
      [],
      {
        project_id: project.id,
        page_number: 0,
        page_size: 25,
      },
    ),
  );
  const row = asArray(observeList).find(
    (candidate) => candidate?.span_id === spanId,
  ) || {
    span_id: spanId,
    trace_id: traceId,
  };
  const detail = observationSpanPayload(
    await client.get(apiPath("/tracer/observation-span/{id}/", { id: spanId })),
  );

  evidence.push({
    endpoint: "observe span lifecycle disposable seed",
    project_id: project.id,
    project_version_id: projectVersionId,
    trace_id: traceId,
    span_id: spanId,
  });
  return { project, span: row, detail, observeList };
}

function journeySafeId(value) {
  return String(value || Date.now().toString(36)).replace(
    /[^a-zA-Z0-9_-]/g,
    "_",
  );
}

async function resolveObserveProjectWithUsers(client, evidence, filters = []) {
  const preferredProjectId =
    process.env.OBSERVE_USERS_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe users project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 50 },
        }),
      );

  let bestMatch = null;
  for (const project of projects) {
    if (!project?.id) continue;
    const users = await client.get(
      queryWithFilters(apiPath("/tracer/users/"), filters, {
        project_id: project.id,
        current_page_index: 0,
        page_size: 10,
      }),
    );
    const rows = asArray(users.table || users).filter(
      (row) => row?.user_id && row?.end_user_id,
    );
    const total = responseCount(users);
    if (!rows.length) continue;
    const candidate = { project, user: rows[0], baseTotal: total };
    if (!bestMatch || total > bestMatch.baseTotal) {
      bestMatch = candidate;
    }
  }

  if (!bestMatch?.user?.user_id) {
    skip(
      "No observe project with end-user rows is available for user coverage.",
    );
  }
  evidence.push({
    endpoint: "observe user project search",
    project_id: bestMatch.project.id,
    user_id: bestMatch.user.user_id,
    base_total: bestMatch.baseTotal,
  });
  return bestMatch;
}

async function resolveObserveVoiceCallForAnnotation(client, evidence) {
  const preferredProjectId =
    process.env.OBSERVE_VOICE_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe voice project" }]
    : asArray(
        await client.get(apiPath("/tracer/project/list_projects/"), {
          query: { page_number: 0, page_size: 100 },
        }),
      );

  for (const project of projects) {
    if (!project?.id) continue;
    const list = await client.get(
      queryWithFilters(apiPath("/tracer/trace/list_voice_calls/"), [], {
        project_id: project.id,
        page: 1,
        page_size: 25,
      }),
    );
    const baseRows = asArray(list).filter((row) => row?.trace_id);
    if (!baseRows.length) continue;

    for (const call of baseRows) {
      const detail = await client.get(
        apiPath("/tracer/trace/voice_call_detail/"),
        {
          query: { trace_id: call.trace_id },
        },
      );
      const rootSpan = findRootConversationSpan(detail?.observation_span);
      if (!rootSpan?.id) continue;

      const queueEntries = await getVoiceQueueEntriesForSource(
        client,
        call.trace_id,
        rootSpan.id,
      );
      const defaultEntry = queueEntries.find(
        (entry) => entry?.queue?.is_default && asArray(entry.labels).length > 0,
      );
      if (!defaultEntry?.queue?.id) continue;
      if (defaultEntry?.item?.id) continue;

      const label =
        asArray(defaultEntry.labels).find((item) => item?.allow_notes) ||
        asArray(defaultEntry.labels)[0];
      if (!label?.id) continue;

      evidence.push({
        endpoint: "observe voice project search",
        project_id: project.id,
        trace_id: call.trace_id,
        root_span_id: rootSpan.id,
        queue_id: defaultEntry.queue.id,
        label_id: label.id,
      });
      return {
        project,
        call,
        detail,
        rootSpanId: rootSpan.id,
        queue: defaultEntry.queue,
        label,
        baseRows,
      };
    }
  }

  skip(
    "No unqueued observe voice call with default queue labels is available for direct annotation coverage.",
  );
}

async function getVoiceQueueEntriesForSource(client, traceId, rootSpanId) {
  return asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: {
        sources: JSON.stringify([
          {
            source_type: "trace",
            source_id: traceId,
            span_notes_source_id: rootSpanId,
          },
        ]),
      },
    }),
  );
}

function findRootConversationSpan(spans) {
  const rows = asArray(spans);
  return (
    rows.find(
      (span) =>
        !span?.parent_span_id && span?.observation_type === "conversation",
    ) ||
    rows.find((span) => !span?.parent_span_id) ||
    rows[0]
  );
}

function scoreValueForVoiceLabel(label, runId) {
  const type = String(label?.type || "").toLowerCase();
  const settings = label?.settings || {};
  if (type.includes("thumb")) return { value: "down" };
  if (type.includes("categorical") || type.includes("select")) {
    const option = asArray(settings.options)[0];
    const selected =
      option?.value ?? option?.id ?? option?.label ?? option?.name;
    return { selected: [String(selected ?? "api journey")] };
  }
  if (type.includes("numeric") || type.includes("number")) return { value: 3 };
  if (type.includes("star")) return { rating: 4 };
  return { text: `OBS-005 direct voice annotation ${runId}` };
}

function valuesEqual(left, right) {
  return (
    JSON.stringify(normalizeJsonValue(left)) ===
    JSON.stringify(normalizeJsonValue(right))
  );
}

function normalizeJsonValue(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

async function resolveObserveSpanWithEvalForFeedback(client, evidence) {
  const preferredProjectId =
    process.env.OBSERVE_FEEDBACK_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  let projects;
  if (preferredProjectId) {
    projects = [
      { id: preferredProjectId, name: "env observe feedback project" },
    ];
  } else {
    const defaultQueues = await listDefaultQueuesByProject(client);
    const queuedProjects = defaultQueues.map((queue) => ({
      id: queue.project,
      name: queue.name,
      defaultQueue: queue,
    }));
    const queuedProjectIds = new Set(
      queuedProjects.map((project) => project.id),
    );
    const remainingProjects = asArray(
      await client.get(apiPath("/tracer/project/list_projects/"), {
        query: { page_number: 0, page_size: 100 },
      }),
    ).filter((project) => !queuedProjectIds.has(project.id));
    projects = [...queuedProjects, ...remainingProjects];
  }

  for (const project of projects) {
    if (!project?.id) continue;
    const list = await client.get(
      apiPath("/tracer/observation-span/list_spans_observe/"),
      { query: { project_id: project.id, page_number: 0, page_size: 25 } },
    );
    const rows = asArray(list.table || list).filter(
      (row) => row?.span_id || row?.id,
    );
    for (const span of rows) {
      const spanId = span.span_id || span.id;
      for (const evalConfigId of evalConfigIdsFromSpanRow(span)) {
        try {
          const detail = await client.get(
            apiPath("/tracer/observation-span/get_evaluation_details/"),
            {
              query: {
                observation_span_id: spanId,
                custom_eval_config_id: evalConfigId,
              },
            },
          );
          evidence.push({
            endpoint: "observe span eval feedback project search",
            project_id: project.id,
            span_id: spanId,
            custom_eval_config_id: evalConfigId,
            queue_id: project.defaultQueue?.id || null,
          });
          return {
            project,
            span,
            evalConfigId,
            evaluationDetail: detail,
            queue: project.defaultQueue || null,
          };
        } catch {
          // Span rows can contain UUID-valued fields that are not eval config ids.
        }
      }
    }
  }

  skip(
    "No observe span with a reloadable eval detail is available for feedback coverage.",
  );
}

async function listDefaultQueuesByProject(client) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100 },
    }),
  );
  return queues.filter(
    (queue) => queue?.is_default && queue?.project && queue?.id,
  );
}

function evalConfigIdsFromSpanRow(row) {
  const ids = [];
  const push = (value) => {
    const stringValue = String(value || "");
    if (isUuidLike(stringValue) && !ids.includes(stringValue))
      ids.push(stringValue);
  };

  for (const [key, value] of Object.entries(row || {})) {
    if (isUuidLike(key)) push(key);
    if (String(key).toLowerCase().includes("eval")) push(value);
    collectNestedEvalConfigIds(value, push);
  }

  return ids;
}

function collectNestedEvalConfigIds(value, push) {
  if (Array.isArray(value)) {
    for (const item of value) collectNestedEvalConfigIds(item, push);
    return;
  }
  if (!value || typeof value !== "object") return;

  push(value.custom_eval_config_id);
  push(value.eval_config_id);
  push(value.config_id);
  if (String(value.category || "").includes("eval")) push(value.id);

  for (const nested of Object.values(value)) {
    if (Array.isArray(nested) || (nested && typeof nested === "object")) {
      collectNestedEvalConfigIds(nested, push);
    }
  }
}

function isUuidLike(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    String(value || ""),
  );
}

async function getObservationSpanQueueEntries(client, spanId) {
  return asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: { source_type: "observation_span", source_id: spanId },
    }),
  );
}

function scoreLabelId(score) {
  return (
    score?.label_id ||
    score?.annotation_label_id ||
    score?.label?.id ||
    score?.label ||
    null
  );
}

async function findAnnotationLabelByName(client, name) {
  const rows = asArray(
    await client.get(apiPath("/model-hub/annotations-labels/"), {
      query: { search: name },
    }),
  );
  return rows.find((label) => label.name === name);
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("not_found") ||
      message.includes("no annotationslabels matches") ||
      message.includes("no feedback matches")
    ) {
      return null;
    }
    throw error;
  }
}

function defaultObserveUserDateFilter(days = 90) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - days);
  return {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: [start.toISOString(), end.toISOString()],
    },
  };
}

function observeChartsDateFilter(days) {
  const end = new Date();
  end.setDate(end.getDate() + 1);
  const start = new Date();
  start.setDate(start.getDate() - days);
  return {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: [start.toISOString(), end.toISOString()],
    },
  };
}

function assertObserveChartsGraph(graph, label) {
  const systemMetrics = graph?.system_metrics || {};
  const summary = {};
  for (const [metric, valueKey] of [
    ["latency", "latency"],
    ["tokens", "tokens"],
    ["traffic", "traffic"],
    ["cost", "cost"],
  ]) {
    const rows = asArray(systemMetrics[metric]);
    assert(rows.length > 0, `${label} chart omitted ${metric} buckets.`);
    for (const row of rows) {
      assert(row?.timestamp, `${label} ${metric} row omitted timestamp.`);
      assert(
        !Number.isNaN(Date.parse(row.timestamp)),
        `${label} ${metric} row returned invalid timestamp ${row.timestamp}.`,
      );
      assert(
        Number.isFinite(Number(row[valueKey] ?? row.value ?? 0)),
        `${label} ${metric} row returned non-numeric value.`,
      );
    }
    summary[`${metric}_points`] = rows.length;
    summary[`${metric}_sum`] = rows.reduce(
      (total, row) => total + Number(row[valueKey] ?? row.value ?? 0),
      0,
    );
  }
  return summary;
}

function rowContainsValue(row, expected) {
  const target = String(expected);
  return Object.values(row || {}).some((value) => valueContains(value, target));
}

function valueContains(value, target) {
  if (Array.isArray(value)) {
    return value.some((item) => valueContains(item, target));
  }
  if (value && typeof value === "object") {
    return Object.values(value).some((item) => valueContains(item, target));
  }
  return String(value ?? "") === target;
}
