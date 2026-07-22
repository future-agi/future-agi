import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import process from "node:process";
import { promisify } from "node:util";

import {
  apiPath,
  asArray,
  assert,
  createApiClient,
  currentUserEmail,
  currentUserId,
  envFlag,
  isUuid,
  requireMutations,
  skip,
  withQuery,
} from "../lib/api-client.mjs";
import {
  annotationValueForLabel,
  canonicalTextFilter,
  assertCurrentUserResolved,
  getQueueLabels,
  queuePath,
  resolveQueue,
  resolveQueueItem,
} from "../lib/fixtures.mjs";

const execFileAsync = promisify(execFile);

export const annotationQueueJourneys = [
  {
    id: "AQ-API-001",
    title:
      "Queue list, detail, progress, analytics, agreement, and export fields",
    tags: ["annotation", "safe", "smoke"],
    async run({ client, evidence }) {
      const queue = await resolveQueue(client, evidence);
      const queueId = queue.id;

      const detail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
      );
      assert(
        detail?.id === queueId,
        "Queue detail did not return the requested id.",
      );

      const progress = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", { id: queueId }),
      );
      assert(
        typeof progress.total === "number",
        "Queue progress response did not include total.",
      );

      const analytics = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/analytics/", {
          id: queueId,
        }),
      );
      assert(
        analytics && typeof analytics === "object",
        "Queue analytics response was not an object.",
      );

      const exportFields = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/export-fields/", {
          id: queueId,
        }),
      );
      assert(
        asArray(exportFields.fields).length > 0,
        "Queue export fields did not include any fields.",
      );
      assert(
        asArray(exportFields.default_mapping).length > 0,
        "Queue export fields did not include a default mapping.",
      );

      let agreementStatus = "checked";
      try {
        await client.get(
          apiPath("/model-hub/annotation-queues/{id}/agreement/", {
            id: queueId,
          }),
        );
      } catch (error) {
        if (![402, 403].includes(error.status)) throw error;
        agreementStatus = `entitlement blocked (${error.status})`;
      }

      evidence.push({
        queue_id: queueId,
        progress_total: progress.total,
        export_field_count: asArray(exportFields.fields).length,
        agreement_status: agreementStatus,
      });
    },
  },
  {
    id: "AQ-API-030",
    title:
      "Queue create entitlement gate, workspace scope, labels, and creator roles",
    tags: ["annotation", "mutating", "db-audit", "create"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const namePrefix = `api journey queue create ${runId}`;
      const queueName = `${namePrefix} main`;
      const labelName = `${namePrefix} label`;

      const missingNameStatus = await expectHttpStatus(
        () =>
          client.post(apiPath("/model-hub/annotation-queues/"), {
            description: "Missing name validation coverage.",
          }),
        400,
      );

      const otherLabelFixture = await insertOtherWorkspaceQueueLabelFixtureDb({
        namePrefix,
        organizationId,
        userId,
      });
      cleanup.defer("delete other-workspace queue label fixture", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );
      const crossWorkspaceLabelStatus = await expectHttpStatus(
        () =>
          client.post(apiPath("/model-hub/annotation-queues/"), {
            name: `${namePrefix} cross workspace label`,
            label_ids: [otherLabelFixture.label_id],
          }),
        400,
      );

      let queue;
      let createMode = "api";
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description: "Disposable queue for API journey create coverage.",
          instructions: "Verify labels and creator roles.",
          annotations_required: 1,
          reservation_timeout_minutes: 45,
          requires_review: false,
          auto_assign: false,
        });
      } catch (error) {
        if (error.status !== 402) throw error;
        createMode = "db_seeded_after_create_entitlement";
        evidence.push({
          create_entitlement_status: error.status,
          create_entitlement_body: error.body,
        });
        queue = await insertAnnotationQueueCreateFixtureDb({
          queueName,
          organizationId,
          workspaceId,
          userId,
        });
      }
      assert(queue?.id, "Queue create/seed did not produce a queue id.");
      cleanup.defer("hard-delete queue create fixture", () =>
        hardDeleteQueueIfPresent(client, queue.id, queueName),
      );

      const labelResponse = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for queue create coverage.",
          settings: {
            placeholder: "Queue create coverage",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const label = labelResponse?.id
        ? labelResponse
        : await findAnnotationLabelByName(client, labelName);
      assert(label?.id, "Could not resolve created queue label by name.");
      cleanup.defer("delete queue create label", () =>
        client.delete(
          apiPath("/model-hub/annotations-labels/{id}/", { id: label.id }),
        ),
      );

      let requiredLabelDenied = false;
      let addedLabel;
      try {
        addedLabel = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: label.id, required: true },
        );
      } catch (error) {
        if (error.status !== 402) throw error;
        requiredLabelDenied = true;
        evidence.push({
          required_label_entitlement_status: error.status,
          required_label_entitlement_body: error.body,
        });
        addedLabel = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: label.id, required: false },
        );
      }
      assert(
        addedLabel?.label?.id === label.id,
        "Add-label response did not return the created label.",
      );

      const detail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      assert(
        detail?.id === queue.id,
        "Queue detail did not reload created queue.",
      );
      assert(
        detail.name === queueName &&
          detail.description ===
            "Disposable queue for API journey create coverage." &&
          detail.instructions === "Verify labels and creator roles.",
        `Queue detail did not preserve create fields: ${JSON.stringify(detail)}.`,
      );
      const creator = findQueueAnnotator(detail, userId);
      assert(
        creator &&
          creator.role === "manager" &&
          sameJsonValue(asArray(creator.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]),
        `Queue detail did not expose creator full roles: ${JSON.stringify(creator)}.`,
      );
      assert(
        asArray(detail.viewer_roles).includes("manager") &&
          asArray(detail.viewer_roles).includes("reviewer") &&
          asArray(detail.viewer_roles).includes("annotator"),
        `Queue detail viewer_roles did not include full creator access: ${JSON.stringify(
          detail.viewer_roles,
        )}.`,
      );
      assert(
        asArray(detail.labels).some(
          (queueLabel) => String(queueLabel.label_id) === String(label.id),
        ),
        "Queue detail did not include the attached label.",
      );

      const searched = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { search: queueName, include_counts: true },
        }),
      );
      const searchedQueue = searched.find((row) => row.id === queue.id);
      assert(
        searchedQueue &&
          Number(searchedQueue.label_count || 0) >= 1 &&
          Number(searchedQueue.annotator_count || 0) >= 1,
        `Queue list/search did not include counts for the created queue: ${JSON.stringify(
          searched,
        )}.`,
      );

      const dbAudit = await loadQueueCreateDbAudit({
        queueId: queue.id,
        userId,
        labelId: label.id,
      });
      assert(
        String(dbAudit.organization_id) === String(organizationId) &&
          String(dbAudit.workspace_id) === String(workspaceId),
        `Queue DB scope mismatch: ${JSON.stringify(dbAudit)}.`,
      );
      assert(
        dbAudit.creator_member?.role === "manager" &&
          sameJsonValue(asArray(dbAudit.creator_member?.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]),
        `Creator DB membership mismatch: ${JSON.stringify(dbAudit)}.`,
      );
      assert(
        Number(dbAudit.active_label_count) === 1 &&
          Number(dbAudit.label_binding_count) === 1 &&
          Number(dbAudit.active_member_count) === 1,
        `Queue DB label/member counts mismatch: ${JSON.stringify(dbAudit)}.`,
      );

      evidence.push({
        queue_create_mode: createMode,
        queue_id: queue.id,
        queue_name: queueName,
        label_id: label.id,
        missing_name_status: missingNameStatus,
        cross_workspace_label_status: crossWorkspaceLabelStatus,
        required_label_denied: requiredLabelDenied,
        active_label_count: dbAudit.active_label_count,
        active_member_count: dbAudit.active_member_count,
        creator_roles: dbAudit.creator_member?.roles,
        workspace_id: dbAudit.workspace_id,
      });
    },
  },
  {
    id: "AQ-API-034",
    title: "Queue list filters, archived view, and duplicate-create payload",
    tags: ["annotation", "mutating", "list", "duplicate", "db-audit"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const namePrefix = `api journey queue list duplicate ${runId}`;
      const baseQueueName = `${namePrefix} base`;
      const duplicateQueueName = `Copy of ${baseQueueName}`;
      await deleteQueueCreateFixturesDb(namePrefix);
      cleanup.defer("delete queue list/duplicate fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const basePayload = {
        name: baseQueueName,
        description:
          "Disposable queue for list/search/filter/duplicate coverage.",
        instructions: "Verify queue list filters and duplicate create payload.",
        assignment_strategy: "manual",
        annotations_required: 1,
        reservation_timeout_minutes: 37,
        requires_review: false,
        auto_assign: false,
      };

      let baseQueue;
      let createMode = "api";
      try {
        baseQueue = await client.post(
          apiPath("/model-hub/annotation-queues/"),
          basePayload,
        );
      } catch (error) {
        if (error.status !== 402) throw error;
        createMode = "db_seeded_after_create_entitlement";
        evidence.push({
          create_entitlement_status: error.status,
          create_entitlement_body: error.body,
        });
        baseQueue = await insertAnnotationQueueCreateFixtureDb({
          queueName: baseQueueName,
          organizationId,
          workspaceId,
          userId,
        });
      }
      assert(
        baseQueue?.id,
        "Queue list/duplicate setup did not create a queue.",
      );
      cleanup.defer("hard-delete base queue list fixture", () =>
        hardDeleteQueueIfPresent(client, baseQueue.id, baseQueueName),
      );

      const pageSizeRows = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { search: namePrefix, page_size: 1 },
        }),
      );
      assert(
        pageSizeRows.length === 1 && pageSizeRows[0]?.id === baseQueue.id,
        `Queue list page_size alias did not return the base queue as a single-row page: ${JSON.stringify(
          pageSizeRows,
        )}.`,
      );

      const searchRows = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { search: namePrefix, include_counts: true, limit: 100 },
        }),
      );
      const searchedBase = searchRows.find((row) => row.id === baseQueue.id);
      assert(
        searchedBase &&
          Object.prototype.hasOwnProperty.call(searchedBase, "label_count") &&
          Object.prototype.hasOwnProperty.call(
            searchedBase,
            "annotator_count",
          ) &&
          Object.prototype.hasOwnProperty.call(searchedBase, "item_count"),
        `Queue search/include_counts did not expose the base queue counts: ${JSON.stringify(
          searchRows,
        )}.`,
      );

      const baseStatus = baseQueue.status || searchedBase.status || "active";
      const statusRows = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: {
            search: namePrefix,
            status: baseStatus,
            include_counts: true,
            limit: 100,
          },
        }),
      );
      assert(
        statusRows.some((row) => row.id === baseQueue.id) &&
          statusRows.every((row) => row.status === baseStatus),
        `Queue status filter did not isolate ${baseStatus} rows: ${JSON.stringify(
          statusRows,
        )}.`,
      );

      await client.delete(
        apiPath("/model-hub/annotation-queues/{id}/", { id: baseQueue.id }),
      );
      const defaultRowsAfterArchive = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { search: baseQueueName, limit: 100 },
        }),
      );
      assert(
        !defaultRowsAfterArchive.some((row) => row.id === baseQueue.id),
        `Default queue list included an archived queue: ${JSON.stringify(
          defaultRowsAfterArchive,
        )}.`,
      );

      const archivedRows = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: {
            archived: true,
            search: baseQueueName,
            include_counts: true,
            limit: 100,
          },
        }),
      );
      assert(
        archivedRows.some((row) => row.id === baseQueue.id),
        `Archived queue list did not include the archived queue: ${JSON.stringify(
          archivedRows,
        )}.`,
      );

      await client.post(
        apiPath("/model-hub/annotation-queues/{id}/restore/", {
          id: baseQueue.id,
        }),
      );
      const restoredDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: baseQueue.id }),
      );
      assert(
        restoredDetail?.id === baseQueue.id && restoredDetail.deleted !== true,
        `Queue restore did not return an active queue detail: ${JSON.stringify(
          restoredDetail,
        )}.`,
      );

      const duplicatePayload = {
        name: duplicateQueueName,
        description: restoredDetail.description || "",
        instructions: restoredDetail.instructions || "",
        assignment_strategy: restoredDetail.assignment_strategy || "manual",
        annotations_required: restoredDetail.annotations_required || 1,
        reservation_timeout_minutes:
          restoredDetail.reservation_timeout_minutes || 60,
        requires_review: restoredDetail.requires_review === true,
        auto_assign: restoredDetail.auto_assign === true,
        label_ids: asArray(restoredDetail.labels)
          .map((queueLabel) => labelId(queueLabel))
          .filter(Boolean),
        annotator_ids: asArray(restoredDetail.annotators)
          .map((annotator) => annotator.user_id)
          .filter(Boolean),
        annotator_roles: buildQueueAnnotatorRoleMap(restoredDetail.annotators),
      };

      let duplicateQueue;
      let duplicateCreateMode = "api";
      try {
        duplicateQueue = await client.post(
          apiPath("/model-hub/annotation-queues/"),
          duplicatePayload,
        );
      } catch (error) {
        if (error.status !== 402) throw error;
        duplicateCreateMode = "entitlement_blocked";
        evidence.push({
          duplicate_create_entitlement_status: error.status,
          duplicate_create_entitlement_body: error.body,
        });
      }

      if (duplicateQueue?.id) {
        cleanup.defer("hard-delete duplicate queue fixture", () =>
          hardDeleteQueueIfPresent(
            client,
            duplicateQueue.id,
            duplicateQueueName,
          ),
        );
        assert(
          duplicateQueue.id !== baseQueue.id &&
            duplicateQueue.name === duplicateQueueName,
          `Duplicate-create response reused the source queue or wrong name: ${JSON.stringify(
            duplicateQueue,
          )}.`,
        );
        const duplicateDetail = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/", {
            id: duplicateQueue.id,
          }),
        );
        assert(
          duplicateDetail.description === duplicatePayload.description &&
            duplicateDetail.instructions === duplicatePayload.instructions &&
            duplicateDetail.assignment_strategy ===
              duplicatePayload.assignment_strategy &&
            Number(duplicateDetail.annotations_required) ===
              Number(duplicatePayload.annotations_required) &&
            Number(duplicateDetail.reservation_timeout_minutes) ===
              Number(duplicatePayload.reservation_timeout_minutes) &&
            duplicateDetail.requires_review ===
              duplicatePayload.requires_review,
          `Duplicate queue did not preserve copied settings: ${JSON.stringify({
            duplicatePayload,
            duplicateDetail,
          })}.`,
        );
        const duplicateSearchRows = asArray(
          await client.get(apiPath("/model-hub/annotation-queues/"), {
            query: { search: duplicateQueueName, limit: 100 },
          }),
        );
        assert(
          duplicateSearchRows.some((row) => row.id === duplicateQueue.id),
          `Queue search did not find duplicate-created queue: ${JSON.stringify(
            duplicateSearchRows,
          )}.`,
        );
      }

      const dbAudit = await loadQueueListDuplicateDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assert(
        Number(dbAudit.wrong_scope_count) === 0 &&
          Number(dbAudit.matching_total_count) >= (duplicateQueue?.id ? 2 : 1),
        `Queue list/duplicate DB audit mismatch: ${JSON.stringify(dbAudit)}.`,
      );

      evidence.push({
        queue_create_mode: createMode,
        duplicate_create_mode: duplicateCreateMode,
        base_queue_id: baseQueue.id,
        duplicate_queue_id: duplicateQueue?.id || null,
        status_filter: baseStatus,
        page_size_alias_match_count: pageSizeRows.length,
        search_match_count: searchRows.length,
        archived_match_count: archivedRows.length,
        matching_total_count: dbAudit.matching_total_count,
        wrong_scope_count: dbAudit.wrong_scope_count,
      });
    },
  },
  {
    id: "AQ-API-035",
    title: "Organization user member picker scope and read-only mutation guard",
    tags: ["annotation", "mutating", "member-picker", "organizations", "users"],
    async run({ client, user, organizationId, evidence }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const email = currentUserEmail(user);
      assert(email, "Current user email is required for org member search.");

      const orgUsersPath = apiPath(
        "/model-hub/organizations/{organization_id}/users/",
        { organization_id: organizationId },
      );
      const orgUserDetailPath = apiPath(
        "/model-hub/organizations/{organization_id}/users/{id}/",
        { organization_id: organizationId, id: userId },
      );

      const rows = asArray(
        await client.get(orgUsersPath, { query: { limit: 30 } }),
      );
      assert(
        rows.some((row) => row.id === userId && row.email === email),
        `Organization user list did not include the current user: ${JSON.stringify(
          rows,
        )}.`,
      );

      const activeRows = asArray(
        await client.get(orgUsersPath, {
          query: { is_active: true, search: email, limit: 30 },
        }),
      );
      assert(
        activeRows.length === 1 && activeRows[0]?.id === userId,
        `Organization user search/is_active=true did not isolate the current user: ${JSON.stringify(
          activeRows,
        )}.`,
      );

      const inactiveRows = asArray(
        await client.get(orgUsersPath, {
          query: { is_active: false, search: email, limit: 30 },
        }),
      );
      assert(
        inactiveRows.length === 0,
        `Organization user is_active=false returned active current user rows: ${JSON.stringify(
          inactiveRows,
        )}.`,
      );

      const detail = await client.get(orgUserDetailPath);
      assert(
        detail?.id === userId && detail?.email === email,
        `Organization user detail did not return the current user: ${JSON.stringify(
          detail,
        )}.`,
      );

      const wrongOrgId = randomUUID();
      const wrongOrgPath = apiPath(
        "/model-hub/organizations/{organization_id}/users/",
        { organization_id: wrongOrgId },
      );
      const wrongOrgStatus = await expectHttpStatus(
        () => client.get(wrongOrgPath),
        404,
      );

      const mutationStatuses = {
        post: await expectHttpStatus(
          () =>
            client.post(orgUsersPath, {
              email: `aq-api-035-${Date.now()}@futureagi.local`,
              name: "AQ API 035 should not create",
            }),
          405,
        ),
        put: await expectHttpStatus(
          () =>
            client.put(orgUserDetailPath, {
              email,
              name: "AQ API 035 should not replace",
            }),
          405,
        ),
        patch: await expectHttpStatus(
          () =>
            client.patch(orgUserDetailPath, {
              name: "AQ API 035 should not patch",
            }),
          405,
        ),
        delete: await expectHttpStatus(
          () => client.delete(orgUserDetailPath),
          405,
        ),
      };

      evidence.push({
        organization_id: organizationId,
        current_user_id: userId,
        list_count: rows.length,
        active_search_count: activeRows.length,
        inactive_search_count: inactiveRows.length,
        wrong_org_status: wrongOrgStatus,
        mutation_statuses: mutationStatuses,
      });
    },
  },
  {
    id: "AQ-API-002",
    title:
      "Queue item read paths, annotate detail, annotations, discussion, next item",
    tags: ["annotation", "safe", "smoke"],
    async run({ client, evidence }) {
      const queue = await resolveQueue(client, evidence);
      const item = await resolveQueueItem(client, queue.id, evidence);

      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        {
          query: {
            include_completed: true,
            include_all_annotations: true,
          },
        },
      );
      assert(
        detail?.item?.id === item.id,
        "Annotate detail returned wrong item.",
      );
      assert(
        Array.isArray(detail.labels),
        "Annotate detail labels must be an array.",
      );
      assert(
        Array.isArray(detail.annotations),
        "Annotate detail annotations must be an array.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(detail, "existing_notes"),
        "Annotate detail must include existing_notes for whole-item notes.",
      );

      const annotations = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
          queue.id,
          { id: item.id },
        ),
      );
      assert(
        Array.isArray(annotations),
        "Item annotations response must be an array.",
      );

      const discussion = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
          queue.id,
          { id: item.id },
        ),
      );
      assert(
        Array.isArray(discussion.review_comments),
        "Discussion response must include review_comments.",
      );
      assert(
        Array.isArray(discussion.review_threads),
        "Discussion response must include review_threads.",
      );

      const next = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/next-item/",
          queue.id,
        ),
        { query: { include_completed: true } },
      );
      assert(
        Object.prototype.hasOwnProperty.call(next, "item"),
        "Next-item response must include item key.",
      );

      evidence.push({
        item_id: item.id,
        label_count: detail.labels.length,
        annotation_count: annotations.length,
        discussion_threads: discussion.review_threads.length,
      });
    },
  },
  {
    id: "AQ-API-003",
    title: "Bulk assignment updates assigned-to-me list and DB state",
    tags: ["annotation", "mutating", "assignment", "db-audit"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        sample.traceId,
        sample.projectId,
        evidence,
      );
      await ensureExplicitQueueMember(
        client,
        queue,
        userId,
        cleanup,
        evidence,
        {
          reason: "assignment coverage",
          roles: ["manager", "reviewer", "annotator"],
        },
      );

      const itemPayloads = [
        {
          source_type: "trace",
          source_id: sample.traceId,
          status: "pending",
          priority: 4,
          order: 7101,
          metadata: { api_journey: runId, stage: "assignment-trace" },
        },
        {
          source_type: "observation_span",
          source_id: sample.spanId,
          status: "pending",
          priority: 4,
          order: 7102,
          metadata: { api_journey: runId, stage: "assignment-span" },
        },
      ];
      const createdItems = [];
      for (const payload of itemPayloads) {
        const created = await client.post(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          payload,
        );
        assert(
          created?.id,
          "Assignment journey queue item create returned no id.",
        );
        createdItems.push({ ...created, expected: payload });
        cleanup.defer(`delete assignment queue item ${created.id}`, () =>
          deleteQueueItemIfPresent(client, queue.id, created.id),
        );
      }
      const itemIds = createdItems.map((item) => item.id);

      const assigned = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: itemIds,
          user_ids: [userId],
          action: "set",
        },
      );
      assert(
        Number(assigned.assigned) === itemIds.length,
        `Bulk assign returned wrong count: ${JSON.stringify(assigned)}.`,
      );
      cleanup.defer("clear assignment journey item assignments", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queue.id,
          ),
          {
            item_ids: itemIds,
            user_ids: [],
            action: "set",
          },
        ),
      );

      const mine = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              assigned_to: "me",
              status: ["pending", "in_progress", "completed", "skipped"],
              limit: 500,
            },
          },
        ),
      );
      assert(
        itemIds.every((itemId) =>
          mine.some((row) => String(row.id) === String(itemId)),
        ),
        "Assigned-to-me list did not include every item immediately after assignment.",
      );

      const assignedDetails = [];
      const assignedDbAudits = [];
      for (const item of createdItems) {
        const detail = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: item.id },
          ),
        );
        assert(
          String(detail.assigned_to || "") === String(userId) &&
            asArray(detail.assigned_users).some(
              (assignedUser) => String(assignedUser.id) === String(userId),
            ),
          `Assigned item detail did not include the current user: ${JSON.stringify(
            detail,
          )}.`,
        );
        assignedDetails.push(detail);
        const dbAudit = await loadQueueItemDbAudit(item.id);
        assertQueueItemDbState(dbAudit, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: item.expected.source_type,
          sourceId: item.expected.source_id,
          status: "pending",
          priority: item.expected.priority,
          order: item.expected.order,
          metadataStage: item.expected.metadata.stage,
          deleted: false,
        });
        assert(
          String(dbAudit.assigned_to_id || "") === String(userId) &&
            Number(dbAudit.active_assignments) === 1 &&
            asArray(dbAudit.assignment_user_ids).some(
              (assignedUserId) => String(assignedUserId) === String(userId),
            ),
          `Assigned item DB state did not include the current user: ${JSON.stringify(
            dbAudit,
          )}.`,
        );
        assignedDbAudits.push(dbAudit);
      }

      const progress = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queue.id,
        }),
      );
      const currentUserStats = asArray(progress.annotator_stats).find(
        (stats) => String(stats.user_id) === String(userId),
      );
      assert(
        Number(progress.user_progress?.pending || 0) >= itemIds.length &&
          Number(currentUserStats?.pending || 0) >= itemIds.length,
        `Progress did not count assigned pending items for current user: ${JSON.stringify(
          progress,
        )}.`,
      );

      const cleared = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: itemIds,
          user_ids: [],
          action: "set",
        },
      );
      assert(
        Number(cleared.assigned) === 0,
        `Assignment clear returned wrong count: ${JSON.stringify(cleared)}.`,
      );
      for (const item of createdItems) {
        const dbAudit = await loadQueueItemDbAudit(item.id);
        assert(
          !dbAudit.assigned_to_id &&
            Number(dbAudit.active_assignments) === 0 &&
            asArray(dbAudit.assignment_user_ids).length === 0,
          `Assignment clear did not remove active DB assignments: ${JSON.stringify(
            dbAudit,
          )}.`,
        );
      }

      for (const item of createdItems) {
        await deleteQueueItemIfPresent(client, queue.id, item.id);
        const cleanupDb = await loadQueueItemDbAudit(item.id);
        assertQueueItemDbState(cleanupDb, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: item.expected.source_type,
          sourceId: item.expected.source_id,
          status: "pending",
          priority: item.expected.priority,
          order: item.expected.order,
          metadataStage: item.expected.metadata.stage,
          deleted: true,
        });
      }

      evidence.push({
        queue_id: queue.id,
        queue_name: queue.name,
        item_ids: itemIds,
        trace_id: sample.traceId,
        span_id: sample.spanId,
        assigned_to: userId,
        assigned_response_count: assigned.assigned,
        assigned_to_me_count: mine.filter((row) =>
          itemIds.includes(String(row.id)),
        ).length,
        assigned_detail_count: assignedDetails.length,
        active_assignment_counts: assignedDbAudits.map((row) =>
          Number(row.active_assignments),
        ),
        user_progress_pending: progress.user_progress?.pending,
        annotator_pending: currentUserStats?.pending,
        cleanup_deleted: true,
        organization_id: organizationId,
        workspace_id: workspaceId,
      });
    },
  },
  {
    id: "AQ-API-004",
    title:
      "Discussion comment, wide emoji reaction, resolve, and reopen lifecycle",
    tags: ["annotation", "mutating", "comments", "db-audit"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        sample.traceId,
        sample.projectId,
        evidence,
      );
      await ensureExplicitQueueMember(
        client,
        queue,
        userId,
        cleanup,
        evidence,
        {
          reason: "discussion mention coverage",
        },
      );
      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace"],
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));
      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        { items: [{ source_type: "trace", source_id: sample.traceId }] },
      );
      const afterItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace"],
            },
          },
        ),
      );
      const createdItems = afterItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      assert(
        added.added === 1 && createdItems.length === 1,
        `Discussion journey expected one new queue item, added=${added.added} saw=${createdItems.length}.`,
      );
      const item = createdItems[0];
      cleanup.defer("delete discussion queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, item.id),
      );

      const email = currentUserEmail(user);
      const marker = `api-journey-discussion ${runId}`;
      const commentText = `${marker} root #item @${email || "current-user"}`;

      const created = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
          queue.id,
          { id: item.id },
        ),
        { comment: commentText, mentioned_user_ids: [userId] },
      );
      const commentId = created?.comment?.id;
      const threadId = created?.thread?.id || created?.comment?.thread;
      assert(commentId, "Discussion create did not return comment.id.");
      assert(threadId, "Discussion create did not return thread.id.");
      assert(
        asArray(created.comment?.mentioned_users).some(
          (mentioned) => String(mentioned.id) === String(userId),
        ),
        "Discussion create did not preserve explicit/current-user mention.",
      );

      const replyText = `${marker} reply`;
      const replied = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
          queue.id,
          { id: item.id },
        ),
        { comment: replyText, thread_id: threadId },
      );
      const replyId = replied?.comment?.id;
      assert(replyId, "Discussion reply did not return comment.id.");

      const searched = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
          queue.id,
          { id: item.id },
        ),
        { query: { search: marker } },
      );
      assert(
        asArray(searched.review_comments).some(
          (comment) => comment.id === commentId,
        ) &&
          asArray(searched.review_comments).some(
            (comment) => comment.id === replyId,
          ),
        "Discussion search did not return both marker comments.",
      );

      const reacted = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/reaction/",
          queue.id,
          { id: item.id, comment_id: commentId },
        ),
        { emoji: "\u{1F44D}" },
      );
      const reactedComment =
        reacted.comment ||
        asArray(reacted.review_comments).find((row) => row.id === commentId);
      const reactions = Array.isArray(reactedComment?.reactions)
        ? reactedComment.reactions
        : Object.entries(reactedComment?.reactions || {}).map(
            ([emoji, userIds]) => ({
              emoji,
              user_ids: Array.isArray(userIds) ? userIds : [],
            }),
          );
      assert(
        reactions.some((reaction) => reaction.emoji === "\u{1F44D}"),
        "Reaction payload did not contain the selected emoji.",
      );

      const resolved = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
          queue.id,
          { id: item.id, thread_id: threadId },
        ),
        { comment: `resolved by api journey ${runId}` },
      );
      const resolvedThread =
        resolved.thread ||
        asArray(resolved.review_threads).find((row) => row.id === threadId);
      assert(
        resolvedThread?.status === "resolved",
        "Resolve did not mark the thread as resolved.",
      );
      const resolveCommentId = resolved?.comment?.id || null;

      const reopened = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/reopen/",
          queue.id,
          { id: item.id, thread_id: threadId },
        ),
        { comment: `reopened by api journey ${runId}` },
      );
      const reopenedThread =
        reopened.thread ||
        asArray(reopened.review_threads).find((row) => row.id === threadId);
      assert(
        ["open", "reopened"].includes(reopenedThread?.status),
        "Reopen did not mark the thread as open/reopened.",
      );
      const reopenCommentId = reopened?.comment?.id || null;

      const reloaded = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
          queue.id,
          { id: item.id },
        ),
      );
      assert(
        asArray(reloaded.review_threads).some(
          (thread) =>
            String(thread.id) === String(threadId) &&
            thread.status === "reopened",
        ),
        "Discussion read did not show the reopened thread.",
      );

      const activeDbAudit = await loadDiscussionDbAudit(threadId);
      assertDiscussionDbState(activeDbAudit, {
        queueItemId: item.id,
        organizationId,
        workspaceId,
        threadStatus: "reopened",
        expectedDeleted: false,
        rootCommentId: commentId,
        replyCommentId: replyId,
        resolveCommentId,
        reopenCommentId,
        userId,
        emoji: "\u{1F44D}",
      });

      await deleteQueueItemIfPresent(client, queue.id, item.id);
      const cleanupDbAudit = await loadDiscussionDbAudit(threadId);
      assertDiscussionDbState(cleanupDbAudit, {
        queueItemId: item.id,
        organizationId,
        workspaceId,
        threadStatus: "reopened",
        expectedDeleted: true,
        rootCommentId: commentId,
        replyCommentId: replyId,
        resolveCommentId,
        reopenCommentId,
        userId,
        emoji: "\u{1F44D}",
      });

      evidence.push({
        queue_id: queue.id,
        item_id: item.id,
        trace_id: sample.traceId,
        comment_id: commentId,
        reply_comment_id: replyId,
        resolve_comment_id: resolveCommentId,
        reopen_comment_id: reopenCommentId,
        thread_id: threadId,
        search_matches: asArray(searched.review_comments).length,
        db_comment_count: activeDbAudit.comments.length,
        cleanup_deleted_thread: cleanupDbAudit.thread.deleted,
        organization_id: organizationId,
        workspace_id: workspaceId,
      });
    },
  },
  {
    id: "AQ-API-005",
    title:
      "Annotation label create, attach to queue, remove, update, and delete",
    tags: ["annotation", "mutating", "labels"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const queue = await resolveQueue(client, evidence);
      const labelName = `api journey label ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Created by API journey regression.",
          settings: {
            placeholder: "API journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : await findAnnotationLabelByName(client, labelName);
      assert(createdLabel?.id, "Label create could not be resolved by name.");
      cleanup.defer("delete annotation label", () =>
        client.delete(
          apiPath("/model-hub/annotations-labels/{id}/", {
            id: createdLabel.id,
          }),
        ),
      );

      const added = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/add-label/", {
          id: queue.id,
        }),
        { label_id: createdLabel.id, required: false },
      );
      assert(
        added?.label?.id === createdLabel.id,
        "Queue add-label returned wrong label.",
      );

      await client.put(
        apiPath("/model-hub/annotations-labels/{id}/", { id: createdLabel.id }),
        {
          name: `${labelName} renamed`,
          type: "text",
          description: "Renamed by API journey regression.",
          settings: {
            placeholder: "API journey renamed",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: false,
        },
      );

      const removed = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
          id: queue.id,
        }),
        { label_id: createdLabel.id },
      );
      assert(
        removed?.removed === true,
        "Queue remove-label did not return removed.",
      );

      evidence.push({ label_id: createdLabel.id, queue_id: queue.id });
    },
  },
  {
    id: "AQ-API-029",
    title: "Queue member multi-role update persists and restores",
    tags: ["annotation", "mutating", "members", "db-audit"],
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
      const userId = assertCurrentUserResolved(user);
      let resolved = null;
      let cleanupResult = null;

      try {
        resolved = await resolveMemberRoleQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          runId,
          evidence,
        );
        const { queue, candidate } = resolved;
        const originalDetail = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
        );
        const originalAnnotators = asArray(originalDetail.annotators);
        const originalAnnotatorIds = originalAnnotators
          .map((annotator) => annotator.user_id)
          .filter(Boolean);
        const originalRoles = buildQueueAnnotatorRoleMap(originalAnnotators);
        const originalDb = await loadQueueMemberDbAudit(queue.id, userId);
        if (!candidate?.seeded) {
          cleanup.defer("restore queue member role state", () =>
            restoreQueueAnnotators(
              client,
              queue.id,
              originalAnnotatorIds,
              originalRoles,
            ),
          );
        }

        const targetRoles = ["manager", "reviewer", "annotator"];
        const patchedAnnotatorIds = originalAnnotatorIds.some(
          (annotatorId) => String(annotatorId) === String(userId),
        )
          ? originalAnnotatorIds
          : [...originalAnnotatorIds, userId];
        const patched = await client.patch(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
          {
            annotator_ids: patchedAnnotatorIds,
            annotator_roles: {
              ...originalRoles,
              [String(userId)]: targetRoles,
            },
          },
        );
        const patchedMember = findQueueAnnotator(patched, userId);
        assert(
          patchedMember &&
            patchedMember.role === "manager" &&
            sameJsonValue(asArray(patchedMember.roles), targetRoles),
          `Patched queue detail did not preserve multi-role member state: ${JSON.stringify(
            patchedMember,
          )}.`,
        );

        const patchedDb = await loadQueueMemberDbAudit(queue.id, userId);
        assert(
          patchedDb.active_user_rows === 1 &&
            patchedDb.member?.role === "manager" &&
            sameJsonValue(asArray(patchedDb.member?.roles), targetRoles) &&
            String(patchedDb.organization_id) === String(organizationId) &&
            String(patchedDb.workspace_id) === String(workspaceId),
          `Patched member DB state mismatch: ${JSON.stringify(patchedDb)}.`,
        );

        await restoreQueueAnnotators(
          client,
          queue.id,
          originalAnnotatorIds,
          originalRoles,
        );
        const restoredDetail = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
        );
        const restoredDb = await loadQueueMemberDbAudit(queue.id, userId);
        const restoredMember = findQueueAnnotator(restoredDetail, userId);
        assert(
          Number(restoredDb.active_user_rows) ===
            Number(originalDb.active_user_rows),
          `Restored member active row count mismatch: before=${JSON.stringify(
            originalDb,
          )} after=${JSON.stringify(restoredDb)}.`,
        );
        if (originalDb.active_user_rows) {
          assert(
            restoredMember &&
              restoredDb.member?.role === originalDb.member?.role &&
              sameJsonValue(
                asArray(restoredDb.member?.roles),
                asArray(originalDb.member?.roles),
              ),
            `Restored member roles did not match original state: before=${JSON.stringify(
              originalDb,
            )} after=${JSON.stringify(restoredDb)}.`,
          );
        } else {
          assert(
            !restoredMember,
            `Temporary queue member was still present after restore: ${JSON.stringify(
              restoredMember,
            )}.`,
          );
        }

        if (candidate?.seeded) {
          cleanupResult = await deleteQueueMemberRoleFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          user_id: userId,
          original_active_user_rows: originalDb.active_user_rows,
          original_roles: originalDb.member?.roles || [],
          patched_member_id: patchedDb.member?.id,
          patched_role: patchedDb.member?.role,
          patched_roles: patchedDb.member?.roles,
          patched_active_member_count: patchedDb.active_member_count,
          restored_active_user_rows: restoredDb.active_user_rows,
          restored_roles: restoredDb.member?.roles || [],
          restored_active_member_count: restoredDb.active_member_count,
          organization_id: organizationId,
          workspace_id: workspaceId,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteQueueMemberRoleFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            member_role_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-018",
    title:
      "Annotation label library covers all label types, validation, queue binding, archive, and restore",
    tags: ["annotation", "mutating", "labels", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const queue = await resolveManagerQueue(client, evidence);
      const labels = [];
      const labelSpecs = [
        {
          key: "categorical",
          type: "categorical",
          settings: {
            options: [
              { label: "Pass" },
              { label: "Fail" },
              { label: "Review" },
            ],
            multi_choice: true,
            rule_prompt: "",
            auto_annotate: false,
            strategy: null,
          },
          allow_notes: true,
        },
        {
          key: "numeric",
          type: "numeric",
          settings: {
            min: 0,
            max: 100,
            step_size: 5,
            display_type: "slider",
          },
          allow_notes: false,
        },
        {
          key: "text",
          type: "text",
          settings: {
            placeholder: "Write label evidence",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
        {
          key: "star",
          type: "star",
          settings: { no_of_stars: 5 },
          allow_notes: false,
        },
        {
          key: "thumbs",
          type: "thumbs_up_down",
          settings: {},
          allow_notes: true,
        },
      ];

      for (const spec of labelSpecs) {
        const name = `api journey label matrix ${runId} ${spec.key}`;
        await client.post(apiPath("/model-hub/annotations-labels/"), {
          name,
          type: spec.type,
          description: `Disposable ${spec.key} label for API journey coverage.`,
          settings: spec.settings,
          allow_notes: spec.allow_notes,
        });
        const label = await findAnnotationLabelByName(client, name);
        assert(label?.id, `Could not resolve created ${spec.key} label.`);
        labels.push({ ...spec, id: label.id, name });
        cleanup.defer(`delete ${spec.key} annotation label`, () =>
          deleteAnnotationLabelIfPresent(client, label.id),
        );
      }

      await expectHttpStatus(
        () =>
          client.post(apiPath("/model-hub/annotations-labels/"), {
            name: `api journey invalid numeric ${runId}`,
            type: "numeric",
            settings: {
              min: 10,
              max: 5,
              step_size: 1,
              display_type: "slider",
            },
          }),
        400,
      );
      await expectHttpStatus(
        () =>
          client.post(apiPath("/model-hub/annotations-labels/"), {
            name: `api journey duplicate categorical ${runId}`,
            type: "categorical",
            settings: {
              options: [{ label: "Yes" }, { label: "yes" }],
              multi_choice: false,
              rule_prompt: "",
              auto_annotate: false,
              strategy: null,
            },
          }),
        400,
      );
      await expectHttpStatus(
        () =>
          client.post(apiPath("/model-hub/annotations-labels/"), {
            name: labels[0].name,
            type: labels[0].type,
            settings: labels[0].settings,
          }),
        400,
      );

      for (const spec of labelSpecs) {
        const byType = asArray(
          await client.get(apiPath("/model-hub/annotations-labels/"), {
            query: {
              type: spec.type,
              search: runId,
              include_usage_count: true,
            },
          }),
        );
        assert(
          byType.some(
            (label) =>
              label.name === labels.find((l) => l.key === spec.key).name,
          ),
          `Label list did not include ${spec.key} label with type filter.`,
        );
        const found = byType.find(
          (label) => label.name === labels.find((l) => l.key === spec.key).name,
        );
        assert(
          Object.prototype.hasOwnProperty.call(
            found || {},
            "annotation_count",
          ) &&
            Object.prototype.hasOwnProperty.call(
              found || {},
              "trace_annotations_count",
            ),
          `${spec.key} label did not include usage count fields.`,
        );
      }

      const numeric = labels.find((label) => label.key === "numeric");
      await client.patch(
        apiPath("/model-hub/annotations-labels/{id}/", { id: numeric.id }),
        {
          name: `${numeric.name} renamed`,
          settings: { ...numeric.settings, max: 110 },
        },
      );
      const numericDetail = await client.get(
        apiPath("/model-hub/annotations-labels/{id}/", { id: numeric.id }),
      );
      assert(
        numericDetail.name === `${numeric.name} renamed` &&
          Number(numericDetail.settings?.max) === 110,
        "Numeric label PATCH did not round-trip through detail.",
      );
      numeric.name = numericDetail.name;
      numeric.settings = numericDetail.settings;

      const categorical = labels.find((label) => label.key === "categorical");
      let requiredLabelCovered = false;
      try {
        const requiredAdded = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: categorical.id, required: true },
        );
        requiredLabelCovered = requiredAdded?.label?.required === true;
      } catch (error) {
        if (![402, 403].includes(error.status)) throw error;
        evidence.push({
          required_label_blocked_status: error.status,
          required_label_blocked_detail: String(
            error.body?.detail || error.message || "",
          ).slice(0, 240),
        });
        const optionalAdded = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: categorical.id, required: false },
        );
        assert(
          optionalAdded?.label?.required === false,
          "Optional queue label add did not return required=false.",
        );
      }
      cleanup.defer("remove label matrix queue binding", () =>
        removeQueueLabelIfPresent(client, queue.id, categorical.id),
      );

      const queueDetailWithLabel = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      const queueLabel = asArray(
        queueDetailWithLabel.labels ||
          queueDetailWithLabel.queue_labels ||
          queueDetailWithLabel.annotation_labels,
      ).find((label) => String(labelId(label)) === String(categorical.id));
      assert(queueLabel, "Queue detail did not include the added label.");
      if (requiredLabelCovered) {
        assert(
          queueLabel.required === true,
          "Queue detail did not mark label required.",
        );
      }

      const archived = await client.delete(
        apiPath("/model-hub/annotations-labels/{id}/", { id: labels[4].id }),
      );
      assert(
        archived?.status === true ||
          archived?.deleted === true ||
          archived === "" ||
          archived === null,
        `Label archive returned unexpected shape: ${JSON.stringify(archived)}.`,
      );
      const archivedList = asArray(
        await client.get(apiPath("/model-hub/annotations-labels/"), {
          query: { search: labels[4].name, include_archived: true },
        }),
      );
      assert(
        archivedList.some(
          (label) =>
            String(label.id) === String(labels[4].id) &&
            label.name === labels[4].name,
        ),
        "Archived label was not visible through include_archived=true.",
      );
      const restored = await client.post(
        apiPath("/model-hub/annotations-labels/{id}/restore/", {
          id: labels[4].id,
        }),
        {},
      );
      assert(
        String(restored?.id) === String(labels[4].id),
        `Label restore response had wrong shape: ${JSON.stringify(restored)}.`,
      );

      const removed = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
          id: queue.id,
        }),
        { label_id: categorical.id },
      );
      assert(
        removed?.removed === true,
        "Queue label remove did not return removed.",
      );

      evidence.push({
        queue_id: queue.id,
        label_ids: labels.map((label) => label.id),
        label_types: labels.map((label) => label.type),
        required_label_covered: requiredLabelCovered,
        restored_label_id: labels[4].id,
      });
    },
  },
  {
    id: "AQ-API-019",
    title: "Dataset rows add to queue with duplicate guard and source readback",
    tags: ["annotation", "mutating", "dataset", "add-items", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const dataset = await createDisposableDatasetWithRows(
        client,
        cleanup,
        runId,
        evidence,
      );
      const queue = await resolveManagerQueueForDatasetRows(
        client,
        dataset.id,
        evidence,
      );

      const addPayload = {
        items: dataset.rowIds.map((rowId) => ({
          source_type: "dataset_row",
          source_id: rowId,
        })),
      };
      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        addPayload,
      );
      assert(
        Number(added?.added) === dataset.rowIds.length,
        `Dataset row add-items added wrong count: ${JSON.stringify(added)}.`,
      );
      assert(
        asArray(added?.errors).length === 0,
        `Dataset row add-items returned errors: ${JSON.stringify(added)}.`,
      );

      const duplicate = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            {
              source_type: "dataset_row",
              source_id: dataset.rowIds[0],
            },
          ],
        },
      );
      assert(
        Number(duplicate?.added) === 0 && Number(duplicate?.duplicates) === 1,
        `Dataset row duplicate guard returned wrong result: ${JSON.stringify(
          duplicate,
        )}.`,
      );

      const listedItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 100,
              source_type: ["dataset_row"],
              status: ["pending", "in_progress", "completed", "skipped"],
            },
          },
        ),
      );
      const createdItems = [];
      for (const rowId of dataset.rowIds) {
        const entry = await findQueueEntryForSource(
          client,
          queue.id,
          "dataset_row",
          rowId,
        );
        assert(
          entry?.item?.id && String(entry.item.source_id) === String(rowId),
          `for-source did not map dataset row ${rowId} back to the queue item.`,
        );
        createdItems.push(entry.item);

        const itemDetail = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: entry.item.id },
          ),
        );
        assert(
          itemDetail?.source_type === "dataset_row" &&
            String(itemDetail?.source_preview?.dataset_id) ===
              String(dataset.id),
          `Dataset row item detail did not expose the expected source preview: ${JSON.stringify(
            itemDetail,
          )}.`,
        );
      }

      const createdIds = new Set(createdItems.map((item) => String(item.id)));
      assert(
        createdItems.every((createdItem) =>
          listedItems.some(
            (item) =>
              String(item.id) === String(createdItem.id) &&
              createdIds.has(String(item.id)) &&
              item.source_type === "dataset_row" &&
              String(item.source_preview?.dataset_id) === String(dataset.id),
          ),
        ),
        "Dataset row queue items were not visible through source_type-filtered list.",
      );

      cleanup.defer("delete dataset row journey queue items", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queue.id, item.id);
        }
      });

      evidence.push({
        queue_id: queue.id,
        dataset_id: dataset.id,
        dataset_column_id: dataset.columnId,
        dataset_row_ids: dataset.rowIds,
        queue_item_ids: createdItems.map((item) => item.id),
        added_count: added.added,
        duplicate_count: duplicate.duplicates,
      });
    },
  },
  {
    id: "AQ-API-032",
    title: "Queue plan-limit count recovery after hard delete and retry",
    tags: ["annotation", "mutating", "limits", "db-audit", "create"],
    async run({
      client,
      cleanup,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const namePrefix = `api journey queue limit ${runId}`;
      const firstQueueName = `${namePrefix} first`;
      const retryQueueName = `${namePrefix} retry`;
      cleanup.defer("hard-delete queue limit DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const before = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });

      let firstQueue;
      try {
        firstQueue = await createPlanLimitProbeQueue(client, firstQueueName);
      } catch (error) {
        if (error.status !== 402) throw error;
        const bodyText = JSON.stringify(error.body || {});
        assert(
          bodyText.includes("ENTITLEMENT_LIMIT") ||
            bodyText.includes("annotation queues limit"),
          `Queue create limit error did not expose entitlement details: ${bodyText}.`,
        );
        const afterBlockedCreate = await loadQueueLimitDbAudit({
          organizationId,
          workspaceId,
          namePrefix,
        });
        assert(
          Number(afterBlockedCreate.matching_total_count) === 0,
          `Blocked queue create still left rows: ${JSON.stringify(
            afterBlockedCreate,
          )}.`,
        );
        evidence.push({
          create_mode: "entitlement_limit_observed",
          create_status: error.status,
          create_error: error.body,
          before_org_active_count: before.org_active_count,
          before_workspace_active_count: before.workspace_active_count,
          after_blocked_org_active_count: afterBlockedCreate.org_active_count,
        });
        return;
      }

      assert(firstQueue?.id, "First queue limit probe create returned no id.");
      cleanup.defer("hard-delete first queue limit probe", () =>
        hardDeleteQueueIfPresent(client, firstQueue.id, firstQueueName),
      );

      const afterFirstCreate = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(before, afterFirstCreate, 1, "first create");

      await hardDeleteQueueIfPresent(client, firstQueue.id, firstQueueName);
      const missingFirstStatus = await expectHttpStatus(
        () =>
          client.get(
            apiPath("/model-hub/annotation-queues/{id}/", {
              id: firstQueue.id,
            }),
          ),
        404,
      );

      const afterFirstDelete = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(before, afterFirstDelete, 0, "first hard delete");

      const retryQueue = await createPlanLimitProbeQueue(
        client,
        retryQueueName,
      );
      assert(retryQueue?.id, "Retry queue limit probe create returned no id.");
      cleanup.defer("hard-delete retry queue limit probe", () =>
        hardDeleteQueueIfPresent(client, retryQueue.id, retryQueueName),
      );

      const afterRetryCreate = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(before, afterRetryCreate, 1, "retry create");

      await hardDeleteQueueIfPresent(client, retryQueue.id, retryQueueName);
      const finalAudit = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(before, finalAudit, 0, "final cleanup");

      evidence.push({
        create_mode: "api_create_delete_retry",
        first_queue_id: firstQueue.id,
        retry_queue_id: retryQueue.id,
        before_org_active_count: before.org_active_count,
        after_first_create_org_active_count: afterFirstCreate.org_active_count,
        after_first_delete_org_active_count: afterFirstDelete.org_active_count,
        after_retry_create_org_active_count: afterRetryCreate.org_active_count,
        final_org_active_count: finalAudit.org_active_count,
        before_workspace_active_count: before.workspace_active_count,
        final_workspace_active_count: finalAudit.workspace_active_count,
        missing_first_status: missingFirstStatus,
        final_matching_total_count: finalAudit.matching_total_count,
      });
    },
  },
  {
    id: "MUR-API-001",
    title: "Creator all-role annotate/review mode separation",
    tags: ["annotation", "mutating", "multi-user", "review", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const creatorId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second workspace annotator to run multi-role mode coverage.",
        );
      }
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      assert(altUserId, "Alternate annotator user id could not be resolved.");
      if (String(altUserId) === String(creatorId)) {
        skip("Alternate token resolved to the creator user.");
      }

      const sample = await resolveTraceAndSpanSample(client);
      const namePrefix = `api journey multi role ${runId}`;
      const queueName = `${namePrefix} queue`;
      const labelName = `${namePrefix} text label`;
      cleanup.defer("hard-delete multi-role DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const labelResponse = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for multi-role mode coverage.",
          settings: {
            placeholder: "Multi-role coverage",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const label = labelResponse?.id
        ? labelResponse
        : await findAnnotationLabelByName(client, labelName);
      assert(label?.id, "Could not resolve created multi-role label.");
      cleanup.defer("delete multi-role label", () =>
        deleteAnnotationLabelIfPresent(client, label.id),
      );

      let queue;
      let reviewWorkflowMode = "enabled";
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description: "Disposable queue for multi-role coverage.",
          instructions: "Verify creator annotate and review modes.",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: true,
          auto_assign: false,
          label_ids: [label.id],
          annotator_ids: [creatorId, altUserId],
          annotator_roles: {
            [String(creatorId)]: ["manager", "reviewer", "annotator"],
            [String(altUserId)]: ["annotator"],
          },
        });
      } catch (error) {
        if (error.status === 402 || error.status === 403) {
          reviewWorkflowMode = "entitlement_blocked";
          evidence.push({
            review_workflow_entitlement_status: error.status,
            review_workflow_entitlement_body: error.body,
          });
          queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
            name: queueName,
            description: "Disposable queue for multi-role coverage.",
            instructions: "Verify creator annotate and review-mode reads.",
            annotations_required: 1,
            reservation_timeout_minutes: 30,
            requires_review: false,
            auto_assign: false,
            label_ids: [label.id],
            annotator_ids: [creatorId, altUserId],
            annotator_roles: {
              [String(creatorId)]: ["manager", "reviewer", "annotator"],
              [String(altUserId)]: ["annotator"],
            },
          });
        } else {
          throw error;
        }
      }
      assert(queue?.id, "Multi-role queue create returned no id.");
      cleanup.defer("hard-delete multi-role queue", () =>
        hardDeleteQueueIfPresent(client, queue.id, queueName),
      );

      await restoreQueueStatusIfNeeded(client, queue.id, "active");
      const reviewWorkflowEnabled = reviewWorkflowMode === "enabled";

      const creatorQueueDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      const creatorMember = asArray(creatorQueueDetail.annotators).find(
        (annotator) => String(annotator.user_id) === String(creatorId),
      );
      const altMember = asArray(creatorQueueDetail.annotators).find(
        (annotator) => String(annotator.user_id) === String(altUserId),
      );
      assert(
        asArray(creatorQueueDetail.viewer_roles).includes("manager") &&
          asArray(creatorQueueDetail.viewer_roles).includes("reviewer") &&
          asArray(creatorQueueDetail.viewer_roles).includes("annotator") &&
          sameJsonValue(asArray(creatorMember?.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]) &&
          sameJsonValue(asArray(altMember?.roles), ["annotator"]),
        `Multi-role queue detail exposed wrong memberships: ${JSON.stringify(
          creatorQueueDetail,
        )}.`,
      );

      const altQueueDetail = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      assert(
        sameJsonValue(asArray(altQueueDetail.viewer_roles), ["annotator"]),
        `Alternate user should only see annotator role: ${JSON.stringify(
          altQueueDetail.viewer_roles,
        )}.`,
      );

      const creatorItem = await client.post(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
        {
          source_type: "trace",
          source_id: sample.traceId,
          status: "pending",
          priority: 4,
          order: 8101,
          metadata: { api_journey: runId, stage: "multi-role-creator" },
        },
      );
      const altItem = await client.post(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
        {
          source_type: "observation_span",
          source_id: sample.spanId,
          status: "pending",
          priority: 4,
          order: 8102,
          metadata: { api_journey: runId, stage: "multi-role-alt" },
        },
      );
      assert(
        creatorItem?.id && altItem?.id,
        "Multi-role item create returned no ids.",
      );
      cleanup.defer("delete multi-role creator item", () =>
        deleteQueueItemIfPresent(client, queue.id, creatorItem.id),
      );
      cleanup.defer("delete multi-role alternate item", () =>
        deleteQueueItemIfPresent(client, queue.id, altItem.id),
      );

      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: [creatorItem.id],
          user_ids: [creatorId],
          action: "set",
        },
      );
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: [altItem.id],
          user_ids: [altUserId],
          action: "set",
        },
      );

      const creatorAnnotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queue.id,
        { id: creatorItem.id },
      );
      const creatorSubmitPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
        queue.id,
        { id: creatorItem.id },
      );
      const creatorCompletePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
        queue.id,
        { id: creatorItem.id },
      );
      const creatorReviewPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
        queue.id,
        { id: creatorItem.id },
      );
      const creatorValue = { text: `creator all-role value ${runId}` };
      await client.post(creatorSubmitPath, {
        annotations: [
          {
            label_id: label.id,
            value: creatorValue,
            notes: `creator all-role note ${runId}`,
          },
        ],
      });
      await client.post(creatorCompletePath, {
        exclude: [creatorItem.id, altItem.id],
      });

      const creatorOwnDetail = await client.get(creatorAnnotatePath, {
        query: { include_completed: true },
      });
      assert(
        creatorOwnDetail.item?.status ===
          (reviewWorkflowEnabled ? "in_progress" : "completed") &&
          (reviewWorkflowEnabled
            ? creatorOwnDetail.item?.review_status === "pending_review"
            : !creatorOwnDetail.item?.review_status) &&
          asArray(creatorOwnDetail.annotations).some(
            (score) =>
              String(score.annotator) === String(creatorId) &&
              sameJsonValue(score.value, creatorValue),
          ),
        `Creator annotate mode did not keep own pending-review draft: ${JSON.stringify(
          creatorOwnDetail,
        )}.`,
      );
      const creatorReviewDetail = await client.get(creatorAnnotatePath, {
        query: reviewWorkflowEnabled
          ? { review_status: "pending_review", include_completed: true }
          : {
              view_mode: "review",
              include_completed: true,
              include_all_annotations: true,
            },
      });
      assert(
        asArray(creatorReviewDetail.annotations).some(
          (score) => String(score.annotator) === String(creatorId),
        ),
        "Creator review mode could not open the mode-test item.",
      );
      let selfReviewStatus = null;
      if (reviewWorkflowEnabled) {
        const selfReview = await expectHttpError(
          () => client.post(creatorReviewPath, { action: "approve" }),
          403,
          "cannot review your own annotation",
        );
        selfReviewStatus = selfReview.status;
      }

      const altAnnotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queue.id,
        { id: altItem.id },
      );
      const altSubmitPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
        queue.id,
        { id: altItem.id },
      );
      const altCompletePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
        queue.id,
        { id: altItem.id },
      );
      const altReviewPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
        queue.id,
        { id: altItem.id },
      );
      const altValue = { text: `alternate annotator value ${runId}` };
      await altClient.get(altAnnotatePath, {
        query: { include_completed: true },
      });
      await altClient.post(altSubmitPath, {
        annotations: [
          {
            label_id: label.id,
            value: altValue,
            notes: `alternate annotator note ${runId}`,
          },
        ],
      });
      await altClient.post(altCompletePath, {
        exclude: [creatorItem.id, altItem.id],
      });

      let reviewNextItemId = null;
      let reviewCommentResult = null;
      let finalAltDetail;
      if (reviewWorkflowEnabled) {
        const reviewNext = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/next-item/",
            queue.id,
          ),
          { query: { view_mode: "review", review_status: "pending_review" } },
        );
        assert(
          String(reviewNext.item?.id) === String(altItem.id),
          `Review navigation did not choose the non-self pending-review item: ${JSON.stringify(
            reviewNext,
          )}.`,
        );
        reviewNextItemId = reviewNext.item?.id || null;

        const pendingReviewItems = asArray(
          await client.get(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/items/",
              queue.id,
            ),
            { query: { review_status: "pending_review", limit: 100 } },
          ),
        );
        assert(
          pendingReviewItems.some(
            (item) => String(item.id) === String(creatorItem.id),
          ) &&
            pendingReviewItems.some(
              (item) => String(item.id) === String(altItem.id),
            ),
          `Pending-review list did not expose both mode-test items: ${JSON.stringify(
            pendingReviewItems,
          )}.`,
        );
      }

      const altReviewDetail = await client.get(altAnnotatePath, {
        query: reviewWorkflowEnabled
          ? { view_mode: "review", review_status: "pending_review" }
          : {
              view_mode: "review",
              include_completed: true,
              include_all_annotations: true,
            },
      });
      assert(
        (reviewWorkflowEnabled
          ? altReviewDetail.item?.review_status === "pending_review"
          : altReviewDetail.item?.status === "completed") &&
          asArray(altReviewDetail.annotations).some(
            (score) =>
              String(score.annotator) === String(altUserId) &&
              sameJsonValue(score.value, altValue),
          ),
        "Creator review mode did not load alternate annotator submission.",
      );

      if (reviewWorkflowEnabled) {
        const approveNote = `all-role reviewer approved ${runId}`;
        const approved = await client.post(altReviewPath, {
          action: "approve",
          notes: approveNote,
        });
        assert(
          approved.action === "approve" &&
            asArray(approved.review_comments).some(
              (comment) =>
                comment.action === "approve" && comment.comment === approveNote,
            ),
          `Approve response did not include reviewer approval comment: ${JSON.stringify(
            approved,
          )}.`,
        );
        finalAltDetail = await client.get(altAnnotatePath, {
          query: { include_completed: true, include_all_annotations: true },
        });
        assert(
          finalAltDetail.item?.status === "completed" &&
            finalAltDetail.item?.review_status === "approved",
          `Approved alternate item did not reload as completed/approved: ${JSON.stringify(
            finalAltDetail.item,
          )}.`,
        );
      } else {
        const commentText = `all-role review-mode comment ${runId}`;
        const reviewComment = await client.post(altReviewPath, {
          action: "comment",
          notes: commentText,
        });
        assert(
          reviewComment.action === "comment" &&
            asArray(reviewComment.review_comments).some(
              (comment) =>
                comment.action === "comment" && comment.comment === commentText,
            ),
          `Review-mode comment did not persist on non-review queue: ${JSON.stringify(
            reviewComment,
          )}.`,
        );
        reviewCommentResult = "comment_saved";
        finalAltDetail = await client.get(altAnnotatePath, {
          query: { include_completed: true, include_all_annotations: true },
        });
        assert(
          finalAltDetail.item?.status === "completed" &&
            !finalAltDetail.item?.review_status,
          `Non-review alternate item did not stay completed without review status: ${JSON.stringify(
            finalAltDetail.item,
          )}.`,
        );
      }

      const queueAudit = await loadQueueStatusDbAudit(queue.id);
      const creatorMemberAudit = await loadQueueMemberDbAudit(
        queue.id,
        creatorId,
      );
      const altMemberAudit = await loadQueueMemberDbAudit(queue.id, altUserId);
      const metricsAudit = await loadQueueMetricsDbAudit(queue.id, creatorId);
      const creatorItemAudit = await loadQueueItemDbAudit(creatorItem.id);
      const altItemAudit = await loadQueueItemDbAudit(altItem.id);
      assert(
        queueAudit.requires_review === reviewWorkflowEnabled &&
          String(queueAudit.organization_id) === String(organizationId) &&
          String(queueAudit.workspace_id) === String(workspaceId) &&
          sameJsonValue(asArray(creatorMemberAudit.member?.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]) &&
          sameJsonValue(asArray(altMemberAudit.member?.roles), ["annotator"]),
        `Multi-role DB membership audit mismatch: ${JSON.stringify({
          queueAudit,
          creatorMemberAudit,
          altMemberAudit,
        })}.`,
      );
      const scoreRows = asArray(metricsAudit.scores);
      assert(
        scoreRows.some(
          (score) =>
            String(score.queue_item_id) === String(creatorItem.id) &&
            String(score.annotator_id) === String(creatorId) &&
            sameJsonValue(score.value, creatorValue),
        ) &&
          scoreRows.some(
            (score) =>
              String(score.queue_item_id) === String(altItem.id) &&
              String(score.annotator_id) === String(altUserId) &&
              sameJsonValue(score.value, altValue),
          ) &&
          creatorItemAudit.status ===
            (reviewWorkflowEnabled ? "in_progress" : "completed") &&
          altItemAudit.status === "completed" &&
          Number(creatorItemAudit.active_scores) === 1 &&
          Number(altItemAudit.active_scores) === 1 &&
          Number(altItemAudit.active_review_comments) >= 1,
        `Multi-role DB item/score audit mismatch: ${JSON.stringify({
          metricsAudit,
          creatorItemAudit,
          altItemAudit,
        })}.`,
      );

      evidence.push({
        queue_id: queue.id,
        label_id: label.id,
        creator_id: creatorId,
        alt_annotator_id: altUserId,
        creator_roles: creatorMemberAudit.member?.roles,
        alt_roles: altMemberAudit.member?.roles,
        review_workflow_mode: reviewWorkflowMode,
        creator_item_id: creatorItem.id,
        alt_item_id: altItem.id,
        creator_self_review_status: selfReviewStatus,
        review_comment_result: reviewCommentResult,
        review_next_item_id: reviewNextItemId,
        final_alt_status: finalAltDetail.item?.status,
        final_alt_review_status: finalAltDetail.item?.review_status,
        db_score_count: scoreRows.length,
        alt_review_comment_count: altItemAudit.active_review_comments,
      });
    },
  },
  {
    id: "MUR-API-002",
    title: "Bulk assignment refreshes annotator and reviewer queues",
    tags: ["annotation", "mutating", "multi-user", "assignment", "review"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const managerId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second workspace annotator to run multi-user assignment refresh coverage.",
        );
      }
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      assert(altUserId, "Alternate annotator user id could not be resolved.");
      if (String(altUserId) === String(managerId)) {
        skip("Alternate token resolved to the manager user.");
      }

      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveReviewQueueWithAnnotator(
        client,
        altUserId,
        evidence,
      );
      const queueId = queue.id;

      const altQueueDetail = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
      );
      assert(
        asArray(altQueueDetail.viewer_roles).includes("annotator"),
        `Alternate user must have annotator visibility before assignment: ${JSON.stringify(
          altQueueDetail.viewer_roles,
        )}.`,
      );

      const itemPayload = {
        source_type: "observation_span",
        source_id: sample.spanId,
        status: "pending",
        priority: 5,
        order: 8201,
        metadata: { api_journey: runId, stage: "multi-user-bulk-assign" },
      };
      const item = await client.post(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
        itemPayload,
      );
      assert(item?.id, "Multi-user assignment item create returned no id.");
      cleanup.defer("delete multi-user assignment queue item", () =>
        deleteQueueItemIfPresent(client, queueId, item.id),
      );

      const assigned = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queueId,
        ),
        {
          item_ids: [item.id],
          user_ids: [altUserId],
          action: "set",
        },
      );
      assert(
        Number(assigned.assigned) === 1,
        `Multi-user assign returned wrong count: ${JSON.stringify(assigned)}.`,
      );
      cleanup.defer("clear multi-user assignment item assignment", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queueId,
          ),
          {
            item_ids: [item.id],
            user_ids: [],
            action: "set",
          },
        ),
      );

      const altAssignedItems = asArray(
        await altClient.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              assigned_to: "me",
              status: ["pending", "in_progress", "completed", "skipped"],
              limit: 500,
              ordering: "-created_at",
            },
          },
        ),
      );
      assert(
        altAssignedItems.some((row) => String(row.id) === String(item.id)),
        "Alternate assigned-to-me list did not include the manager-assigned item immediately.",
      );

      const managerItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              limit: 500,
              ordering: "-created_at",
            },
          },
        ),
      );
      const managerListItem = managerItems.find(
        (row) => String(row.id) === String(item.id),
      );
      assert(
        managerListItem &&
          String(managerListItem.assigned_to || "") === String(altUserId),
        `Manager item list did not refresh with alternate assignment: ${JSON.stringify(
          managerListItem,
        )}.`,
      );

      const itemDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/",
          queueId,
          { id: item.id },
        ),
      );
      assert(
        String(itemDetail.assigned_to || "") === String(altUserId) &&
          asArray(itemDetail.assigned_users).some(
            (assignedUser) => String(assignedUser.id) === String(altUserId),
          ),
        `Assigned item detail did not include alternate annotator: ${JSON.stringify(
          itemDetail,
        )}.`,
      );

      const progressAfterAssign = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queueId,
        }),
      );
      const altStatsAfterAssign = asArray(
        progressAfterAssign.annotator_stats,
      ).find((stats) => String(stats.user_id) === String(altUserId));
      assert(
        Number(altStatsAfterAssign?.pending || 0) >= 1,
        `Manager progress did not count alternate pending assignment: ${JSON.stringify(
          progressAfterAssign,
        )}.`,
      );

      const altProgressAfterAssign = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queueId,
        }),
      );
      assert(
        Number(altProgressAfterAssign.user_progress?.pending || 0) >= 1,
        `Alternate progress did not count its assigned pending item: ${JSON.stringify(
          altProgressAfterAssign,
        )}.`,
      );

      const assignedDbAudit = await loadQueueItemDbAudit(item.id);
      assertQueueItemDbState(assignedDbAudit, {
        queueId,
        organizationId,
        workspaceId,
        sourceType: itemPayload.source_type,
        sourceId: itemPayload.source_id,
        status: "pending",
        priority: itemPayload.priority,
        order: itemPayload.order,
        metadataStage: itemPayload.metadata.stage,
        deleted: false,
      });
      assert(
        String(assignedDbAudit.assigned_to_id || "") === String(altUserId) &&
          Number(assignedDbAudit.active_assignments) === 1 &&
          asArray(assignedDbAudit.assignment_user_ids).some(
            (assignedUserId) => String(assignedUserId) === String(altUserId),
          ),
        `Assigned DB state did not include alternate annotator: ${JSON.stringify(
          assignedDbAudit,
        )}.`,
      );

      const annotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queueId,
        { id: item.id },
      );
      const submitPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
        queueId,
        { id: item.id },
      );
      const completePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
        queueId,
        { id: item.id },
      );

      const altDetail = await altClient.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        altDetail?.item?.id === item.id,
        "Alternate annotator could not open the manager-assigned item.",
      );
      const labels = asArray(altDetail.labels).filter((label) =>
        labelId(label),
      );
      const requiredLabels = labels.filter((label) => label.required);
      const labelsToSubmit = requiredLabels.length ? requiredLabels : labels;
      assert(
        labelsToSubmit.length > 0,
        "Assigned review item did not expose labels to submit.",
      );

      const submittedValues = new Map();
      const annotations = labelsToSubmit.map((label, index) => {
        const value = reviewAnnotationValue(label, runId, `assigned-${index}`);
        submittedValues.set(String(labelId(label)), value);
        return {
          label_id: labelId(label),
          value,
          notes: label.allow_notes ? `assigned label note ${runId}` : "",
        };
      });
      const submitted = await altClient.post(submitPath, {
        annotations,
        item_notes: `assigned item note ${runId}`,
      });
      assert(
        Number(submitted.submitted || 0) === annotations.length,
        `Alternate assignment submission count mismatch: ${JSON.stringify(
          submitted,
        )}.`,
      );
      const completed = await altClient.post(completePath, {
        exclude: [item.id],
      });
      assert(
        completed.completed_item_id === item.id,
        "Alternate complete did not echo the manager-assigned item id.",
      );

      const altAssignedAfterSubmit = asArray(
        await altClient.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              assigned_to: "me",
              status: ["pending", "in_progress", "completed", "skipped"],
              limit: 500,
              ordering: "-created_at",
            },
          },
        ),
      );
      const altAfterSubmitItem = altAssignedAfterSubmit.find(
        (row) => String(row.id) === String(item.id),
      );
      assert(
        altAfterSubmitItem &&
          altAfterSubmitItem.review_status === "pending_review",
        `Alternate assigned-to-me list did not refresh to pending_review after submit: ${JSON.stringify(
          altAfterSubmitItem,
        )}.`,
      );

      const reviewItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              review_status: "pending_review",
              limit: 500,
              ordering: "-created_at",
            },
          },
        ),
      );
      assert(
        reviewItems.some((row) => String(row.id) === String(item.id)),
        "Manager/reviewer pending-review list did not include the submitted assignment.",
      );

      const reviewerDetail = await client.get(annotatePath, {
        query: {
          view_mode: "review",
          review_status: "pending_review",
          include_all_annotations: true,
        },
      });
      assert(
        reviewerDetail.item?.review_status === "pending_review" &&
          asArray(reviewerDetail.annotations).some(
            (score) =>
              String(score.annotator) === String(altUserId) &&
              submittedValues.has(String(score.label_id)) &&
              sameJsonValue(
                score.value,
                submittedValues.get(String(score.label_id)),
              ),
          ),
        "Manager/reviewer detail did not reload alternate submitted score.",
      );

      const progressAfterSubmit = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queueId,
        }),
      );
      const altStatsAfterSubmit = asArray(
        progressAfterSubmit.annotator_stats,
      ).find((stats) => String(stats.user_id) === String(altUserId));
      assert(
        Number(altStatsAfterSubmit?.in_review || 0) >= 1 &&
          Number(altStatsAfterSubmit?.annotations_count || 0) >=
            annotations.length,
        `Manager progress did not move alternate assignment into review: ${JSON.stringify(
          progressAfterSubmit,
        )}.`,
      );

      const altProgressAfterSubmit = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queueId,
        }),
      );
      assert(
        Number(altProgressAfterSubmit.user_progress?.in_review || 0) >= 1 &&
          Number(altProgressAfterSubmit.user_progress?.completed || 0) >= 1,
        `Alternate progress did not reflect submitted pending-review work: ${JSON.stringify(
          altProgressAfterSubmit,
        )}.`,
      );

      const metricsAudit = await loadQueueMetricsDbAudit(queueId, altUserId);
      const dbItem = asArray(metricsAudit.items).find(
        (row) => String(row.id) === String(item.id),
      );
      const dbAssignments = asArray(metricsAudit.assignments).filter(
        (row) => String(row.queue_item_id) === String(item.id),
      );
      const dbScores = asArray(metricsAudit.scores).filter(
        (score) => String(score.queue_item_id) === String(item.id),
      );
      assert(
        dbItem?.status === "in_progress" &&
          dbItem.review_status === "pending_review" &&
          String(dbItem.assigned_to || "") === String(altUserId) &&
          dbAssignments.length === 1 &&
          String(dbAssignments[0].user_id) === String(altUserId) &&
          dbScores.length === annotations.length &&
          dbScores.every(
            (score) =>
              String(score.annotator_id) === String(altUserId) &&
              submittedValues.has(String(score.label_id)) &&
              sameJsonValue(
                score.value,
                submittedValues.get(String(score.label_id)),
              ),
          ),
        `Multi-user assignment DB metrics mismatch: ${JSON.stringify({
          dbItem,
          dbAssignments,
          dbScores,
        })}.`,
      );

      evidence.push({
        queue_id: queueId,
        queue_name: queue.name,
        item_id: item.id,
        manager_id: managerId,
        annotator_id: altUserId,
        assigned_response_count: assigned.assigned,
        assigned_to_me_before_submit: altAssignedItems.filter(
          (row) => String(row.id) === String(item.id),
        ).length,
        manager_list_refreshed: Boolean(managerListItem),
        labels_submitted: annotations.length,
        review_list_item_count: reviewItems.filter(
          (row) => String(row.id) === String(item.id),
        ).length,
        final_item_status: dbItem.status,
        final_review_status: dbItem.review_status,
        db_assignment_count: dbAssignments.length,
        db_score_count: dbScores.length,
        alt_pending_after_assign: altProgressAfterAssign.user_progress?.pending,
        alt_completed_after_submit:
          altProgressAfterSubmit.user_progress?.completed,
        alt_in_review_after_submit:
          altProgressAfterSubmit.user_progress?.in_review,
      });
    },
  },
  {
    id: "MUR-API-003",
    title: "Mentioned member discussion read/reply lifecycle",
    tags: ["annotation", "mutating", "multi-user", "comments", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const managerId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second workspace member to run mention collaboration coverage.",
        );
      }
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      const altEmail = currentUserEmail(altUser);
      assert(altUserId, "Mentioned alternate user id could not be resolved.");
      if (String(altUserId) === String(managerId)) {
        skip("Alternate token resolved to the manager user.");
      }

      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        sample.traceId,
        sample.projectId,
        evidence,
      );
      await ensureExplicitQueueMember(
        client,
        queue,
        managerId,
        cleanup,
        evidence,
        {
          reason: "multi-user mention manager coverage",
          roles: ["manager", "reviewer", "annotator"],
        },
      );
      await ensureTemporaryAnnotatorMember(
        client,
        queue,
        altUserId,
        cleanup,
        evidence,
      );
      const altQueueDetail = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      assert(
        asArray(altQueueDetail.viewer_roles).includes("annotator"),
        `Alternate user must be a queue member to read mentions: ${JSON.stringify(
          altQueueDetail.viewer_roles,
        )}.`,
      );

      const itemPayload = {
        source_type: "trace",
        source_id: sample.traceId,
        status: "pending",
        priority: 4,
        order: 8301,
        metadata: { api_journey: runId, stage: "multi-user-mention" },
      };
      const item = await client.post(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
        itemPayload,
      );
      assert(
        item?.id,
        "Mention journey direct queue item create returned no id.",
      );
      cleanup.defer("delete multi-user mention queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, item.id),
      );

      const marker = `api-journey-mention ${runId}`;
      const rootText = `${marker} root for @${altEmail || "alt-user"}`;
      const discussionPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
        queue.id,
        { id: item.id },
      );
      const created = await client.post(discussionPath, {
        comment: rootText,
        mentioned_user_ids: [
          String(altUserId),
          ...(altEmail ? [`@${String(altEmail).toUpperCase()}`] : []),
        ],
      });
      const commentId = created?.comment?.id;
      const threadId = created?.thread?.id || created?.comment?.thread;
      assert(commentId, "Mention create did not return comment.id.");
      assert(threadId, "Mention create did not return thread.id.");
      assert(
        asArray(created.comment?.mentioned_users).some(
          (mentioned) => String(mentioned.id) === String(altUserId),
        ),
        `Mention create did not preserve alternate user mention: ${JSON.stringify(
          created.comment?.mentioned_users,
        )}.`,
      );

      const altRead = await altClient.get(discussionPath);
      assert(
        asArray(altRead.review_threads).some(
          (thread) => String(thread.id) === String(threadId),
        ) &&
          asArray(altRead.review_comments).some(
            (comment) =>
              String(comment.id) === String(commentId) &&
              asArray(comment.mentioned_users).some(
                (mentioned) => String(mentioned.id) === String(altUserId),
              ),
          ),
        "Mentioned alternate user could not read the thread and explicit mention.",
      );

      const altSearch = await altClient.get(discussionPath, {
        query: { search: marker },
      });
      assert(
        asArray(altSearch.review_comments).some(
          (comment) => String(comment.id) === String(commentId),
        ),
        "Mentioned alternate user search did not find the root mention.",
      );

      const replyText = `${marker} alternate reply`;
      const altReply = await altClient.post(discussionPath, {
        comment: replyText,
        thread_id: threadId,
      });
      const replyId = altReply?.comment?.id;
      assert(replyId, "Mentioned alternate reply did not return comment.id.");
      assert(
        String(
          altReply.comment?.reviewer?.id || altReply.comment?.reviewer_id,
        ) === String(altUserId),
        `Mentioned alternate reply was not attributed to the alternate user: ${JSON.stringify(
          altReply.comment,
        )}.`,
      );

      const altReacted = await altClient.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/reaction/",
          queue.id,
          { id: item.id, comment_id: commentId },
        ),
        { emoji: "\u{1F44D}" },
      );
      const reactedComment =
        altReacted.comment ||
        asArray(altReacted.review_comments).find(
          (row) => String(row.id) === String(commentId),
        );
      const reactions = Array.isArray(reactedComment?.reactions)
        ? reactedComment.reactions
        : Object.entries(reactedComment?.reactions || {}).map(
            ([emoji, userIds]) => ({
              emoji,
              user_ids: Array.isArray(userIds) ? userIds : [],
            }),
          );
      assert(
        reactions.some(
          (reaction) =>
            reaction.emoji === "\u{1F44D}" &&
            asArray(reaction.user_ids).some(
              (userId) => String(userId) === String(altUserId),
            ),
        ),
        "Alternate reaction payload did not contain the alternate user.",
      );

      const managerSearch = await client.get(discussionPath, {
        query: { search: marker },
      });
      assert(
        asArray(managerSearch.review_comments).some(
          (comment) => String(comment.id) === String(commentId),
        ) &&
          asArray(managerSearch.review_comments).some(
            (comment) => String(comment.id) === String(replyId),
          ),
        "Manager search did not return both root and alternate reply comments.",
      );

      const resolved = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/resolve/",
          queue.id,
          { id: item.id, thread_id: threadId },
        ),
        { comment: `${marker} manager resolve` },
      );
      const resolvedThread =
        resolved.thread ||
        asArray(resolved.review_threads).find(
          (thread) => String(thread.id) === String(threadId),
        );
      assert(
        resolvedThread?.status === "resolved",
        "Manager resolve did not mark mentioned thread resolved.",
      );
      const resolveCommentId = resolved?.comment?.id || null;

      const reopened = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/{thread_id}/reopen/",
          queue.id,
          { id: item.id, thread_id: threadId },
        ),
        { comment: `${marker} manager reopen` },
      );
      const reopenedThread =
        reopened.thread ||
        asArray(reopened.review_threads).find(
          (thread) => String(thread.id) === String(threadId),
        );
      assert(
        reopenedThread?.status === "reopened",
        "Manager reopen did not mark mentioned thread reopened.",
      );
      const reopenCommentId = reopened?.comment?.id || null;

      const altReload = await altClient.get(discussionPath);
      assert(
        asArray(altReload.review_threads).some(
          (thread) =>
            String(thread.id) === String(threadId) &&
            thread.status === "reopened",
        ) &&
          asArray(altReload.review_comments).some(
            (comment) => String(comment.id) === String(replyId),
          ),
        "Mentioned alternate user could not reload the reopened thread and reply.",
      );

      const activeDbAudit = await loadDiscussionDbAudit(threadId);
      assertDiscussionDbState(activeDbAudit, {
        queueItemId: item.id,
        organizationId,
        workspaceId,
        threadStatus: "reopened",
        expectedDeleted: false,
        rootCommentId: commentId,
        replyCommentId: replyId,
        resolveCommentId,
        reopenCommentId,
        userId: altUserId,
        emoji: "\u{1F44D}",
      });
      const rootDbComment = asArray(activeDbAudit.comments).find(
        (comment) => String(comment.id) === String(commentId),
      );
      const replyDbComment = asArray(activeDbAudit.comments).find(
        (comment) => String(comment.id) === String(replyId),
      );
      assert(
        String(rootDbComment?.reviewer_id) === String(managerId) &&
          String(replyDbComment?.reviewer_id) === String(altUserId),
        `Mention DB audit attribution mismatch: ${JSON.stringify(
          activeDbAudit.comments,
        )}.`,
      );

      await deleteQueueItemIfPresent(client, queue.id, item.id);
      const cleanupDbAudit = await loadDiscussionDbAudit(threadId);
      assertDiscussionDbState(cleanupDbAudit, {
        queueItemId: item.id,
        organizationId,
        workspaceId,
        threadStatus: "reopened",
        expectedDeleted: true,
        rootCommentId: commentId,
        replyCommentId: replyId,
        resolveCommentId,
        reopenCommentId,
        userId: altUserId,
        emoji: "\u{1F44D}",
      });

      evidence.push({
        queue_id: queue.id,
        queue_name: queue.name,
        item_id: item.id,
        trace_id: sample.traceId,
        manager_id: managerId,
        mentioned_user_id: altUserId,
        root_comment_id: commentId,
        reply_comment_id: replyId,
        thread_id: threadId,
        resolve_comment_id: resolveCommentId,
        reopen_comment_id: reopenCommentId,
        alt_search_matches: asArray(altSearch.review_comments).length,
        manager_search_matches: asArray(managerSearch.review_comments).length,
        db_comment_count: activeDbAudit.comments.length,
        cleanup_deleted_thread: cleanupDbAudit.thread.deleted,
      });
    },
  },
  {
    id: "AQ-API-036",
    title: "Bulk review request-changes and discussion comment edit/delete",
    tags: ["annotation", "mutating", "review", "comments", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const reviewerId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second active annotator to run bulk-review coverage.",
        );
      }

      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      const altEmail = currentUserEmail(altUser);
      assert(altUserId, "Alternate annotator user id could not be resolved.");
      if (String(altUserId) === String(reviewerId)) {
        skip("Alternate token resolved to the reviewer user.");
      }

      const sample = await resolveTraceAndSpanSample(client, {
        cleanup,
        evidence,
        organizationId,
        runId,
        userId: reviewerId,
        workspaceId,
      });
      const queue = await resolveOrCreateReviewQueueWithAnnotator(
        client,
        altUserId,
        reviewerId,
        cleanup,
        evidence,
        runId,
      );
      if (Number(queue.annotations_required || 1) !== 1) {
        skip(
          `Bulk-review journey requires a single-submission review queue, saw annotations_required=${queue.annotations_required}.`,
        );
      }
      const queueId = queue.id;
      const createdItems = [];
      cleanup.defer("delete bulk-review queue items", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queueId, item.id);
        }
      });

      for (const index of [0, 1]) {
        const sourceSpanId = asArray(sample.spanIds)[index] || sample.spanId;
        assert(sourceSpanId, `No source span id available for item ${index}.`);
        const item = await client.post(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            source_type: "observation_span",
            source_id: sourceSpanId,
            status: "pending",
            priority: 4,
            order: 8800 + index,
            metadata: {
              api_journey: runId,
              stage: "bulk-review-discussion",
              index,
            },
          },
        );
        assert(item?.id, "Bulk-review item create returned no id.");
        createdItems.push(item);
      }

      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queueId,
        ),
        {
          item_ids: createdItems.map((item) => item.id),
          user_ids: [altUserId],
          action: "set",
        },
      );
      cleanup.defer("unassign bulk-review queue items", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queueId,
          ),
          {
            item_ids: createdItems.map((item) => item.id),
            user_ids: [],
            action: "set",
          },
        ),
      );

      for (const [index, item] of createdItems.entries()) {
        const annotatePath = queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queueId,
          { id: item.id },
        );
        const detail = await altClient.get(annotatePath, {
          query: { include_completed: true, include_all_annotations: true },
        });
        const labels = asArray(detail.labels).filter((label) => labelId(label));
        const requiredLabels = labels.filter((label) => label.required);
        const labelsToSubmit = requiredLabels.length ? requiredLabels : labels;
        assert(
          labelsToSubmit.length > 0,
          "Bulk-review item did not expose labels for submission.",
        );
        await altClient.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
            queueId,
            { id: item.id },
          ),
          {
            annotations: labelsToSubmit.map((label, labelIndex) => ({
              label_id: labelId(label),
              value: reviewAnnotationValue(
                label,
                runId,
                `bulk-${index}-${labelIndex}`,
              ),
              notes: label.allow_notes
                ? `bulk-review label note ${runId} ${index}-${labelIndex}`
                : "",
            })),
          },
        );
        await altClient.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
            queueId,
            { id: item.id },
          ),
          { exclude: createdItems.map((created) => created.id) },
        );
        const pendingDetail = await client.get(annotatePath, {
          query: { include_completed: true, include_all_annotations: true },
        });
        assert(
          pendingDetail.item?.review_status === "pending_review",
          `Bulk-review setup item did not enter pending_review: ${JSON.stringify(
            pendingDetail.item,
          )}.`,
        );
      }

      const bulkNotes = `bulk review request changes ${runId}`;
      let bulkReviewMode = "reviewed";
      let bulkResponse = null;
      try {
        bulkResponse = await client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/bulk-review/",
            queueId,
          ),
          {
            item_ids: createdItems.map((item) => item.id),
            action: "request_changes",
            notes: bulkNotes,
          },
        );
        assert(
          Number(bulkResponse.reviewed || 0) === createdItems.length &&
            sameJsonValue(
              asArray(bulkResponse.reviewed_item_ids).sort(),
              createdItems.map((item) => String(item.id)).sort(),
            ) &&
            asArray(bulkResponse.errors).length === 0,
          `Bulk review response mismatch: ${JSON.stringify(bulkResponse)}.`,
        );
      } catch (error) {
        if (![402, 403].includes(error.status)) throw error;
        const bodyText = JSON.stringify(error.body || {});
        assert(
          /review|entitlement|workflow/i.test(bodyText),
          `Bulk review entitlement denial did not mention review/workflow: ${bodyText}.`,
        );
        bulkReviewMode = `entitlement_blocked_${error.status}`;
      }

      const itemAudits = [];
      for (const item of createdItems) {
        const audit = await loadQueueItemDbAudit(item.id);
        itemAudits.push(audit);
        if (bulkReviewMode === "reviewed") {
          assert(
            audit.status === "in_progress" &&
              audit.review_status === "rejected" &&
              audit.review_notes === bulkNotes &&
              Number(audit.active_review_threads) >= 1 &&
              Number(audit.active_review_comments) >= 1,
            `Bulk-reviewed item DB audit mismatch: ${JSON.stringify(audit)}.`,
          );
        } else {
          assert(
            audit.review_status === "pending_review" &&
              Number(audit.active_review_comments) === 0,
            `Entitlement-blocked bulk review still mutated item: ${JSON.stringify(
              audit,
            )}.`,
          );
        }
      }

      const discussionItem = createdItems[0];
      const discussionPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
        queueId,
        { id: discussionItem.id },
      );
      const marker = `bulk review discussion ${runId}`;
      const createdDiscussion = await client.post(discussionPath, {
        comment: `${marker} original`,
      });
      const commentId = createdDiscussion?.comment?.id;
      const threadId =
        createdDiscussion?.thread?.id ||
        createdDiscussion?.comment?.thread_id ||
        createdDiscussion?.comment?.thread;
      assert(commentId, "Discussion create did not return comment.id.");
      assert(threadId, "Discussion create did not return thread.id.");

      const editedDiscussion = await client.patch(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
          queueId,
          { id: discussionItem.id, comment_id: commentId },
        ),
        {
          comment: `${marker} edited for @${altEmail || "alt-user"}`,
          mentioned_user_ids: [
            String(altUserId),
            ...(altEmail ? [`@${String(altEmail).toUpperCase()}`] : []),
          ],
        },
      );
      assert(
        editedDiscussion.comment?.comment ===
          `${marker} edited for @${altEmail || "alt-user"}` &&
          asArray(editedDiscussion.comment?.mentioned_users).some(
            (mentioned) => String(mentioned.id) === String(altUserId),
          ),
        `Discussion edit did not persist text and mention: ${JSON.stringify(
          editedDiscussion.comment,
        )}.`,
      );

      const deletedDiscussion = await client.delete(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/comments/{comment_id}/",
          queueId,
          { id: discussionItem.id, comment_id: commentId },
        ),
      );
      assert(
        !asArray(deletedDiscussion.review_comments).some(
          (comment) => String(comment.id) === String(commentId),
        ),
        "Deleted discussion comment still appeared in the live response.",
      );
      const discussionAudit = await loadDiscussionDbAudit(threadId);
      const deletedComment = asArray(discussionAudit.comments).find(
        (comment) => String(comment.id) === String(commentId),
      );
      assert(
        discussionAudit.thread.deleted === true &&
          deletedComment?.deleted === true &&
          String(discussionAudit.thread.queue_item_id) ===
            String(discussionItem.id) &&
          String(discussionAudit.thread.organization_id) ===
            String(organizationId) &&
          String(discussionAudit.thread.workspace_id) === String(workspaceId),
        `Discussion delete DB audit mismatch: ${JSON.stringify(
          discussionAudit,
        )}.`,
      );

      evidence.push({
        queue_id: queueId,
        queue_name: queue.name,
        source_span_id: sample.spanId,
        source_span_ids: asArray(sample.spanIds).length
          ? sample.spanIds
          : [sample.spanId],
        reviewer_id: reviewerId,
        annotator_id: altUserId,
        item_ids: createdItems.map((item) => item.id),
        bulk_review_mode: bulkReviewMode,
        bulk_reviewed_count: bulkResponse?.reviewed || 0,
        bulk_review_item_statuses: itemAudits.map((audit) => ({
          item_id: audit.id,
          status: audit.status,
          review_status: audit.review_status,
          active_review_threads: audit.active_review_threads,
          active_review_comments: audit.active_review_comments,
        })),
        discussion_thread_id: threadId,
        discussion_comment_id: commentId,
        discussion_deleted_thread: discussionAudit.thread.deleted,
        discussion_deleted_comment: deletedComment?.deleted,
      });
    },
  },
  {
    id: "MUR-API-004",
    title: "Legacy queue role backfill preserves member access",
    tags: ["annotation", "mutating", "multi-user", "migration", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const backendContainer = await requireBackendContainerForJourney();
      const creatorId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second workspace member to run legacy role backfill coverage.",
        );
      }
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const reviewerId = currentUserId(altUser);
      assert(
        reviewerId,
        "Legacy role alternate user id could not be resolved.",
      );
      if (String(reviewerId) === String(creatorId)) {
        skip("Alternate token resolved to the creator user.");
      }

      const namePrefix = `api journey legacy roles ${runId}`;
      cleanup.defer("hard-delete legacy role DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const preflight = await loadLegacyRoleBackfillPreflightDb(namePrefix);
      const fixtures = await insertLegacyQueueRoleFixturesDb({
        namePrefix,
        organizationId,
        workspaceId,
        creatorId,
        reviewerId,
      });
      assert(
        fixtures?.legacy_queue_id && fixtures?.missing_creator_queue_id,
        `Legacy role fixture insert returned no queue ids: ${JSON.stringify(
          fixtures,
        )}.`,
      );

      const beforeAudit = await loadLegacyRoleFixtureAuditDb({
        namePrefix,
        creatorId,
        reviewerId,
      });
      const legacyCreatorBefore = findLegacyRoleAuditMember(
        beforeAudit,
        fixtures.legacy_queue_id,
        creatorId,
      );
      const legacyReviewerBefore = findLegacyRoleAuditMember(
        beforeAudit,
        fixtures.legacy_queue_id,
        reviewerId,
      );
      assert(
        legacyCreatorBefore?.role === "manager" &&
          sameJsonValue(asArray(legacyCreatorBefore.roles), []) &&
          legacyReviewerBefore?.role === "reviewer" &&
          sameJsonValue(asArray(legacyReviewerBefore.roles), []) &&
          Number(beforeAudit.missing_creator_membership_count) === 1,
        `Legacy fixture did not start in pre-backfill shape: ${JSON.stringify(
          beforeAudit,
        )}.`,
      );

      const creatorLegacyBeforeDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: fixtures.legacy_queue_id,
        }),
      );
      const reviewerLegacyBeforeDetail = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: fixtures.legacy_queue_id,
        }),
      );
      const creatorMemberBefore = asArray(
        creatorLegacyBeforeDetail.annotators,
      ).find((annotator) => String(annotator.user_id) === String(creatorId));
      const reviewerMemberBefore = asArray(
        reviewerLegacyBeforeDetail.annotators,
      ).find((annotator) => String(annotator.user_id) === String(reviewerId));
      assert(
        sameJsonValue(asArray(creatorMemberBefore?.roles), ["manager"]) &&
          asArray(reviewerLegacyBeforeDetail.viewer_roles).includes(
            "reviewer",
          ) &&
          sameJsonValue(asArray(reviewerMemberBefore?.roles), ["reviewer"]),
        `Legacy fallback API roles did not preserve pre-backfill access: ${JSON.stringify(
          {
            creatorMemberBefore,
            reviewerViewerRoles: reviewerLegacyBeforeDetail.viewer_roles,
            reviewerMemberBefore,
          },
        )}.`,
      );

      const dryRun = await runBackendManageCommand(
        backendContainer,
        "backfill_annotation_queue_roles",
        ["--dry-run"],
      );
      const dryRunSummary = parseAnnotationQueueRoleBackfillSummary(
        dryRun.stdout,
      );
      assert(
        dryRunSummary.dryRun &&
          dryRunSummary.updated >= 2 &&
          dryRunSummary.created >= 1,
        `Legacy role dry-run summary did not include fixture rows: ${dryRun.stdout}`,
      );

      const afterDryRunAudit = await loadLegacyRoleFixtureAuditDb({
        namePrefix,
        creatorId,
        reviewerId,
      });
      assert(
        sameJsonValue(
          asArray(
            findLegacyRoleAuditMember(
              afterDryRunAudit,
              fixtures.legacy_queue_id,
              creatorId,
            )?.roles,
          ),
          [],
        ) && Number(afterDryRunAudit.missing_creator_membership_count) === 1,
        `Legacy role dry-run mutated fixture rows: ${JSON.stringify(
          afterDryRunAudit,
        )}.`,
      );

      const backfill = await runBackendManageCommand(
        backendContainer,
        "backfill_annotation_queue_roles",
      );
      const backfillSummary = parseAnnotationQueueRoleBackfillSummary(
        backfill.stdout,
      );
      assert(
        !backfillSummary.dryRun &&
          backfillSummary.updated >= 2 &&
          backfillSummary.created >= 1,
        `Legacy role backfill summary did not include fixture rows: ${backfill.stdout}`,
      );

      const afterAudit = await loadLegacyRoleFixtureAuditDb({
        namePrefix,
        creatorId,
        reviewerId,
      });
      const legacyCreatorAfter = findLegacyRoleAuditMember(
        afterAudit,
        fixtures.legacy_queue_id,
        creatorId,
      );
      const legacyReviewerAfter = findLegacyRoleAuditMember(
        afterAudit,
        fixtures.legacy_queue_id,
        reviewerId,
      );
      const missingCreatorAfter = findLegacyRoleAuditMember(
        afterAudit,
        fixtures.missing_creator_queue_id,
        creatorId,
      );
      assert(
        sameJsonValue(asArray(legacyCreatorAfter?.roles), [
          "manager",
          "reviewer",
          "annotator",
        ]) &&
          legacyCreatorAfter?.role === "manager" &&
          sameJsonValue(asArray(legacyReviewerAfter?.roles), ["reviewer"]) &&
          legacyReviewerAfter?.role === "reviewer" &&
          sameJsonValue(asArray(missingCreatorAfter?.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]) &&
          Number(afterAudit.missing_creator_membership_count) === 0,
        `Legacy role backfill DB audit mismatch: ${JSON.stringify(afterAudit)}.`,
      );

      const creatorLegacyAfterDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: fixtures.legacy_queue_id,
        }),
      );
      const reviewerLegacyAfterDetail = await altClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: fixtures.legacy_queue_id,
        }),
      );
      const missingCreatorAfterDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: fixtures.missing_creator_queue_id,
        }),
      );
      const creatorMemberAfter = asArray(
        creatorLegacyAfterDetail.annotators,
      ).find((annotator) => String(annotator.user_id) === String(creatorId));
      const reviewerMemberAfter = asArray(
        reviewerLegacyAfterDetail.annotators,
      ).find((annotator) => String(annotator.user_id) === String(reviewerId));
      const missingCreatorMemberAfter = asArray(
        missingCreatorAfterDetail.annotators,
      ).find((annotator) => String(annotator.user_id) === String(creatorId));
      assert(
        sameJsonValue(asArray(creatorMemberAfter?.roles), [
          "manager",
          "reviewer",
          "annotator",
        ]) &&
          sameJsonValue(asArray(missingCreatorMemberAfter?.roles), [
            "manager",
            "reviewer",
            "annotator",
          ]) &&
          asArray(reviewerLegacyAfterDetail.viewer_roles).includes(
            "reviewer",
          ) &&
          sameJsonValue(asArray(reviewerMemberAfter?.roles), ["reviewer"]),
        `Legacy role backfill API readback mismatch: ${JSON.stringify({
          creatorMemberAfter,
          missingCreatorMemberAfter,
          reviewerViewerRoles: reviewerLegacyAfterDetail.viewer_roles,
          reviewerMemberAfter,
        })}.`,
      );

      const cleanupResult = await deleteQueueCreateFixturesDb(namePrefix);
      const residue = await loadLegacyRoleFixtureResidueDb(namePrefix);
      assert(
        Number(cleanupResult.deleted_queues) === 2 &&
          Number(cleanupResult.deleted_queue_members) === 3 &&
          Number(residue.matching_queue_count) === 0 &&
          Number(residue.matching_member_count) === 0,
        `Legacy role fixture cleanup left residue: ${JSON.stringify({
          cleanupResult,
          residue,
        })}.`,
      );

      evidence.push({
        backend_container: backendContainer,
        legacy_queue_id: fixtures.legacy_queue_id,
        missing_creator_queue_id: fixtures.missing_creator_queue_id,
        creator_id: creatorId,
        reviewer_id: reviewerId,
        preexisting_stale_memberships: preflight.stale_membership_count,
        preexisting_missing_creator_memberships:
          preflight.missing_creator_membership_count,
        dry_run_updated: dryRunSummary.updated,
        dry_run_created: dryRunSummary.created,
        backfill_updated: backfillSummary.updated,
        backfill_created: backfillSummary.created,
        creator_roles_after: legacyCreatorAfter.roles,
        reviewer_roles_after: legacyReviewerAfter.roles,
        missing_creator_roles_after: missingCreatorAfter.roles,
        cleanup_deleted_queues: cleanupResult.deleted_queues,
        cleanup_deleted_members: cleanupResult.deleted_queue_members,
      });
    },
  },
  {
    id: "MUR-API-005",
    title: "Multi-user queue create respects queue pricing limits",
    tags: ["annotation", "mutating", "multi-user", "limits", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const backendContainer = await requireBackendContainerForJourney();
      const creatorId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second workspace member to run multi-user queue limit coverage.",
        );
      }
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      assert(altUserId, "Queue limit alternate user id could not be resolved.");
      if (String(altUserId) === String(creatorId)) {
        skip("Alternate token resolved to the creator user.");
      }

      const namePrefix = `api journey multi user limit ${runId}`;
      let temporaryOverrideId = "";
      cleanup.defer("clear temporary queue limit entitlement override", () =>
        clearTemporaryQueueLimitOverride({
          backendContainer,
          organizationId,
          overrideId: temporaryOverrideId,
        }),
      );
      cleanup.defer("hard-delete multi-user limit DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const before = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      const pricingMode = await loadBackendQueuePricingMode(backendContainer);
      const payloadFor = (name) => ({
        name,
        description: "Disposable queue for multi-user queue limit coverage.",
        instructions: "Verify pricing limits do not leave member residue.",
        annotations_required: 1,
        reservation_timeout_minutes: 30,
        requires_review: false,
        auto_assign: false,
        annotator_ids: [creatorId, altUserId],
        annotator_roles: {
          [String(creatorId)]: ["manager", "reviewer", "annotator"],
          [String(altUserId)]: ["annotator"],
        },
      });

      const capacityQueueName = `${namePrefix} capacity holder`;
      const blockedQueueName = `${namePrefix} blocked`;
      const retryQueueName = `${namePrefix} retry`;

      if (pricingMode.is_oss) {
        const firstQueue = await client.post(
          apiPath("/model-hub/annotation-queues/"),
          payloadFor(capacityQueueName),
        );
        const secondQueue = await client.post(
          apiPath("/model-hub/annotation-queues/"),
          payloadFor(blockedQueueName),
        );
        assert(
          firstQueue?.id && secondQueue?.id,
          "OSS queue creates returned no id.",
        );
        assertQueueDetailRoles(
          firstQueue,
          {
            [creatorId]: ["manager", "reviewer", "annotator"],
            [altUserId]: ["annotator"],
          },
          "OSS first create response",
        );
        assertQueueDetailRoles(
          secondQueue,
          {
            [creatorId]: ["manager", "reviewer", "annotator"],
            [altUserId]: ["annotator"],
          },
          "OSS second create response",
        );

        const afterOssCreates = await loadQueueLimitDbAudit({
          organizationId,
          workspaceId,
          namePrefix,
        });
        assertQueueCountDelta(
          before,
          afterOssCreates,
          2,
          "OSS multi-user creates",
        );
        assert(
          Number(afterOssCreates.matching_active_member_count) === 4,
          `OSS multi-user creates did not persist four memberships: ${JSON.stringify(
            afterOssCreates,
          )}.`,
        );

        await hardDeleteQueueIfPresent(
          client,
          firstQueue.id,
          capacityQueueName,
        );
        await hardDeleteQueueIfPresent(
          client,
          secondQueue.id,
          blockedQueueName,
        );
        const finalAudit = await loadQueueLimitDbAudit({
          organizationId,
          workspaceId,
          namePrefix,
        });
        assertQueueCountDelta(before, finalAudit, 0, "OSS multi-user cleanup");

        evidence.push({
          backend_container: backendContainer,
          pricing_mode: pricingMode.mode,
          is_oss: pricingMode.is_oss,
          first_queue_id: firstQueue.id,
          second_queue_id: secondQueue.id,
          before_org_active_count: before.org_active_count,
          after_create_org_active_count: afterOssCreates.org_active_count,
          final_org_active_count: finalAudit.org_active_count,
          final_matching_total_count: finalAudit.matching_total_count,
        });
        return;
      }

      const temporaryLimit = Number(before.org_active_count) + 1;
      const override = await setTemporaryQueueLimitOverride({
        backendContainer,
        organizationId,
        limit: temporaryLimit,
      });
      assert(
        override?.limit === temporaryLimit && override?.override_id,
        `Temporary queue limit override did not return expected data: ${JSON.stringify(
          override,
        )}.`,
      );
      temporaryOverrideId = override.override_id;

      const capacityQueue = await client.post(
        apiPath("/model-hub/annotation-queues/"),
        payloadFor(capacityQueueName),
      );
      assert(capacityQueue?.id, "Capacity queue create returned no id.");
      assertQueueDetailRoles(
        capacityQueue,
        {
          [creatorId]: ["manager", "reviewer", "annotator"],
          [altUserId]: ["annotator"],
        },
        "capacity create response",
      );

      const afterCapacityCreate = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(
        before,
        afterCapacityCreate,
        1,
        "multi-user capacity create",
      );
      assert(
        Number(afterCapacityCreate.matching_active_member_count) === 2,
        `Capacity queue did not create exactly two active memberships: ${JSON.stringify(
          afterCapacityCreate,
        )}.`,
      );

      const blocked = await expectHttpError(
        () =>
          client.post(
            apiPath("/model-hub/annotation-queues/"),
            payloadFor(blockedQueueName),
          ),
        402,
        "ENTITLEMENT_LIMIT",
      );
      const afterBlockedCreate = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assert(
        Number(afterBlockedCreate.matching_active_count) === 1 &&
          Number(afterBlockedCreate.matching_total_count) === 1 &&
          Number(afterBlockedCreate.matching_active_member_count) === 2,
        `Blocked multi-user create left unexpected queue/member rows: ${JSON.stringify(
          afterBlockedCreate,
        )}.`,
      );

      await hardDeleteQueueIfPresent(
        client,
        capacityQueue.id,
        capacityQueueName,
      );
      const afterCapacityDelete = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(
        before,
        afterCapacityDelete,
        0,
        "multi-user capacity hard delete",
      );

      const retryQueue = await client.post(
        apiPath("/model-hub/annotation-queues/"),
        payloadFor(retryQueueName),
      );
      assert(retryQueue?.id, "Retry queue create returned no id.");
      const retryDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: retryQueue.id,
        }),
      );
      assertQueueDetailRoles(
        retryDetail,
        {
          [creatorId]: ["manager", "reviewer", "annotator"],
          [altUserId]: ["annotator"],
        },
        "retry queue detail",
      );

      const afterRetryCreate = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(
        before,
        afterRetryCreate,
        1,
        "multi-user retry create",
      );
      assert(
        Number(afterRetryCreate.matching_active_member_count) === 2,
        `Retry queue did not create exactly two active memberships: ${JSON.stringify(
          afterRetryCreate,
        )}.`,
      );

      await hardDeleteQueueIfPresent(client, retryQueue.id, retryQueueName);
      const finalAudit = await loadQueueLimitDbAudit({
        organizationId,
        workspaceId,
        namePrefix,
      });
      assertQueueCountDelta(before, finalAudit, 0, "multi-user final cleanup");

      const clearOverride = await clearTemporaryQueueLimitOverride({
        backendContainer,
        organizationId,
        overrideId: temporaryOverrideId,
      });
      temporaryOverrideId = "";
      evidence.push({
        backend_container: backendContainer,
        pricing_mode: pricingMode.mode,
        is_oss: pricingMode.is_oss,
        override_id: override.override_id,
        override_plan: override.plan,
        temporary_limit: temporaryLimit,
        before_org_active_count: before.org_active_count,
        capacity_queue_id: capacityQueue.id,
        blocked_status: blocked.status,
        blocked_error: blocked.body,
        retry_queue_id: retryQueue.id,
        final_org_active_count: finalAudit.org_active_count,
        final_matching_total_count: finalAudit.matching_total_count,
        clear_override_deleted: clearOverride.deleted,
      });
    },
  },
  {
    id: "AQ-API-031",
    title:
      "Annotation history, raw scores, value history, and item notes reload",
    tags: ["annotation", "mutating", "history", "db-audit"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const namePrefix = `api journey history ${runId}`;
      const queueName = `${namePrefix} queue`;
      const labelName = `${namePrefix} text label`;
      cleanup.defer("hard-delete annotation history DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      const labelResponse = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for annotation history coverage.",
          settings: {
            placeholder: "History coverage",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const label = labelResponse?.id
        ? labelResponse
        : await findAnnotationLabelByName(client, labelName);
      assert(label?.id, "Could not resolve created history label.");
      cleanup.defer("delete annotation history label", () =>
        deleteAnnotationLabelIfPresent(client, label.id),
      );

      let queue;
      let queueCreateMode = "api";
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description: "Disposable queue for annotation history coverage.",
          instructions: "Submit repeated values and verify history readback.",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: false,
          auto_assign: false,
        });
      } catch (error) {
        if (error.status !== 402) throw error;
        queueCreateMode = "db_seeded_after_create_entitlement";
        evidence.push({
          create_entitlement_status: error.status,
          create_entitlement_body: error.body,
        });
        queue = await insertAnnotationQueueCreateFixtureDb({
          queueName,
          organizationId,
          workspaceId,
          userId,
        });
      }
      assert(queue?.id, "Annotation history queue create returned no id.");
      cleanup.defer("hard-delete annotation history queue", () =>
        hardDeleteQueueIfPresent(client, queue.id, queueName),
      );

      await restoreQueueStatusIfNeeded(client, queue.id, "active");

      await client.post(
        apiPath("/model-hub/annotation-queues/{id}/add-label/", {
          id: queue.id,
        }),
        { label_id: label.id, required: false },
      );

      const item = await client.post(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
        {
          source_type: "trace",
          source_id: sample.traceId,
          status: "pending",
          priority: 3,
          order: 7301,
          metadata: { api_journey: runId, stage: "history" },
        },
      );
      assert(item?.id, "Annotation history queue item create returned no id.");
      cleanup.defer("delete annotation history queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, item.id),
      );

      const values = [
        { text: `history first ${runId}` },
        { text: `history second ${runId}` },
        { text: `history final ${runId}` },
      ];
      const labelNotes = [
        `history label note one ${runId}`,
        `history label note two ${runId}`,
        `history label note final ${runId}`,
      ];
      const itemNotes = [
        `history item note one ${runId}`,
        `history item note two ${runId}`,
        `history item note final ${runId}`,
      ];

      for (let index = 0; index < values.length; index += 1) {
        const submitted = await client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
            queue.id,
            { id: item.id },
          ),
          {
            annotations: [
              {
                label_id: label.id,
                value: values[index],
                notes: labelNotes[index],
              },
            ],
            item_notes: itemNotes[index],
          },
        );
        assert(
          Number(submitted.submitted) === 1,
          `History submit ${index + 1} did not save one score: ${JSON.stringify(
            submitted,
          )}.`,
        );
      }

      const completed = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
          queue.id,
          { id: item.id },
        ),
        { exclude: [item.id] },
      );
      assert(
        completed.completed_item_id === item.id,
        `Complete response did not echo item id: ${JSON.stringify(completed)}.`,
      );

      const history = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
            queue.id,
            { id: item.id },
          ),
        ),
      );
      assert(
        history.length === 1,
        `History returned ${history.length} scores.`,
      );
      const rawScore = history[0];
      assert(
        String(rawScore.label_id) === String(label.id) &&
          String(rawScore.annotator) === String(userId) &&
          sameJsonValue(rawScore.value, values[2]) &&
          rawScore.notes === labelNotes[2] &&
          rawScore.score_source === "human",
        `Raw score history had wrong current value: ${JSON.stringify(rawScore)}.`,
      );
      assert(
        sameJsonValue(
          asArray(rawScore.value_history).map((entry) => entry.value),
          [values[0], values[1]],
        ) && asArray(rawScore.value_history).every((entry) => entry.at),
        `Raw score value_history did not preserve previous values with timestamps: ${JSON.stringify(
          rawScore.value_history,
        )}.`,
      );

      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        detail.item?.status === "completed" &&
          String(detail.existing_notes || "") === itemNotes[2],
        `Annotate detail did not reload completed item notes: ${JSON.stringify(
          detail,
        )}.`,
      );
      const detailScore = asArray(detail.annotations).find(
        (score) => String(score.id) === String(rawScore.id),
      );
      assert(
        detailScore &&
          sameJsonValue(detailScore.value, values[2]) &&
          sameJsonValue(
            asArray(detailScore.value_history).map((entry) => entry.value),
            [values[0], values[1]],
          ),
        `Annotate detail did not reload raw score history: ${JSON.stringify(
          detail.annotations,
        )}.`,
      );

      const completedItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          { query: { status: "completed", limit: 100 } },
        ),
      );
      assert(
        completedItems.some((row) => String(row.id) === String(item.id)),
        `Completed item list did not expose completed history item: ${JSON.stringify(
          completedItems,
        )}.`,
      );

      const dbAudit = await loadAnnotationHistoryDbAudit({
        queueId: queue.id,
        itemId: item.id,
        labelId: label.id,
        scoreId: rawScore.id,
        userId,
      });
      assert(
        String(dbAudit.queue?.organization_id) === String(organizationId) &&
          String(dbAudit.queue?.workspace_id) === String(workspaceId) &&
          String(dbAudit.item?.status) === "completed",
        `Annotation history DB scope/status mismatch: ${JSON.stringify(dbAudit)}.`,
      );
      assert(
        Number(dbAudit.score_count) === 1 &&
          sameJsonValue(dbAudit.score?.value, values[2]) &&
          sameJsonValue(
            asArray(dbAudit.score?.value_history).map((entry) => entry.value),
            [values[0], values[1]],
          ) &&
          dbAudit.score?.notes === labelNotes[2] &&
          dbAudit.item_note?.notes === itemNotes[2],
        `Annotation history DB value mismatch: ${JSON.stringify(dbAudit)}.`,
      );

      evidence.push({
        queue_create_mode: queueCreateMode,
        queue_id: queue.id,
        item_id: item.id,
        label_id: label.id,
        score_id: rawScore.id,
        history_rows: history.length,
        value_history_count: asArray(rawScore.value_history).length,
        final_item_status: dbAudit.item?.status,
        final_item_note: dbAudit.item_note?.notes,
      });
    },
  },
  {
    id: "AQ-API-020",
    title: "Voice call trace add to queue explicit and filter-mode readback",
    tags: [
      "annotation",
      "observe",
      "voice",
      "mutating",
      "add-items",
      "data-roundtrip",
    ],
    async run({ client, cleanup, evidence }) {
      requireMutations();
      const voice = await resolveObserveVoiceCallSource(client, evidence);
      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        voice.traceId,
        voice.project.id,
        evidence,
      );

      const addPayload = {
        items: [{ source_type: "trace", source_id: voice.traceId }],
      };
      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        addPayload,
      );
      assert(
        Number(added?.added) === 1 && asArray(added?.errors).length === 0,
        `Voice call explicit add-items returned unexpected result: ${JSON.stringify(
          added,
        )}.`,
      );

      const duplicate = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        addPayload,
      );
      assert(
        Number(duplicate?.added) === 0 && Number(duplicate?.duplicates) === 1,
        `Voice call duplicate guard returned unexpected result: ${JSON.stringify(
          duplicate,
        )}.`,
      );

      const explicitEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        voice.traceId,
      );
      assert(
        explicitEntry?.item?.id &&
          explicitEntry.item.source_type === "trace" &&
          String(explicitEntry.item.source_id) === String(voice.traceId),
        "for-source did not reload the explicit voice trace queue item.",
      );
      const explicitItemId = explicitEntry.item.id;

      const explicitDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/",
          queue.id,
          { id: explicitItemId },
        ),
      );
      assert(
        explicitDetail?.source_type === "trace" &&
          String(explicitDetail?.source_preview?.project_id) ===
            String(voice.project.id),
        `Voice trace item detail did not preserve trace/project source preview: ${JSON.stringify(
          explicitDetail,
        )}.`,
      );

      const explicitAnnotate = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: explicitItemId },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        explicitAnnotate?.item?.id === explicitItemId,
        "Voice trace annotate-detail did not return the explicit queue item.",
      );

      await deleteQueueItemIfPresent(client, queue.id, explicitItemId);
      await expectItemMissing(client, queue.id, explicitItemId);

      const filterPayload = {
        selection: {
          mode: "filter",
          source_type: "trace",
          project_id: voice.project.id,
          filter: canonicalTextFilter("trace_id", "equals", voice.traceId),
          is_voice_call: true,
          remove_simulation_calls: false,
        },
      };
      const filterAdded = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        filterPayload,
      );
      assert(
        Number(filterAdded?.added) === 1 &&
          Number(filterAdded?.total_matching || 0) >= 1,
        `Voice call filter-mode add returned unexpected result: ${JSON.stringify(
          filterAdded,
        )}.`,
      );

      const filterEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        voice.traceId,
      );
      assert(
        filterEntry?.item?.id &&
          filterEntry.item.source_type === "trace" &&
          String(filterEntry.item.source_id) === String(voice.traceId),
        "for-source did not reload the filter-mode voice trace queue item.",
      );
      const filterItemId = filterEntry.item.id;
      cleanup.defer("delete voice call filter-mode queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, filterItemId),
      );

      const filterDuplicate = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        filterPayload,
      );
      assert(
        Number(filterDuplicate?.added) === 0 &&
          Number(filterDuplicate?.duplicates || 0) >= 1,
        `Voice call filter-mode duplicate guard returned unexpected result: ${JSON.stringify(
          filterDuplicate,
        )}.`,
      );

      const listedItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 100,
              source_type: ["trace"],
              status: ["pending", "in_progress", "completed", "skipped"],
            },
          },
        ),
      );
      assert(
        listedItems.some(
          (item) =>
            String(item.id) === String(filterItemId) &&
            item.source_type === "trace" &&
            String(item.source_preview?.project_id) ===
              String(voice.project.id),
        ),
        "Voice trace filter-mode item was not visible through source_type-filtered list.",
      );

      evidence.push({
        project_id: voice.project.id,
        project_name: voice.project.name || null,
        voice_trace_id: voice.traceId,
        voice_root_span_id: voice.rootSpanId,
        voice_rows_sampled: voice.baseRows.length,
        queue_id: queue.id,
        explicit_queue_item_id: explicitItemId,
        filter_queue_item_id: filterItemId,
        explicit_duplicate_count: duplicate.duplicates,
        filter_total_matching: filterAdded.total_matching,
        filter_duplicate_count: filterDuplicate.duplicates,
      });
    },
  },
  {
    id: "AQ-API-021",
    title: "Observe trace, span, and session filter-mode add parity",
    tags: [
      "annotation",
      "observe",
      "mutating",
      "add-items",
      "filter-mode",
      "data-roundtrip",
    ],
    async run({ client, cleanup, evidence }) {
      requireMutations();
      const sample = await resolveTraceAndSpanSample(client);
      const session = await resolveObserveSessionSource(client, evidence);
      const specs = [
        {
          key: "trace",
          sourceType: "trace",
          sourceId: sample.traceId,
          projectId: sample.projectId,
          filter: canonicalTextFilter("trace_id", "equals", sample.traceId),
          listSourceTypes: ["trace"],
        },
        {
          key: "span",
          sourceType: "observation_span",
          sourceId: sample.spanId,
          projectId: sample.projectId,
          filter: canonicalTextFilter("span_id", "equals", sample.spanId),
          listSourceTypes: ["observation_span"],
        },
        {
          key: "session",
          sourceType: "trace_session",
          sourceId: session.sessionId,
          projectId: session.project.id,
          filter: canonicalTextFilter(
            "session_id",
            "equals",
            session.sessionId,
          ),
          listSourceTypes: ["trace_session"],
        },
      ];
      const created = [];

      for (const spec of specs) {
        const queue = await resolveManagerQueueWithoutSource(
          client,
          spec.sourceType,
          spec.sourceId,
          spec.projectId,
          evidence,
        );
        const payload = {
          selection: {
            mode: "filter",
            source_type: spec.sourceType,
            project_id: spec.projectId,
            filter: spec.filter,
          },
        };
        const added = await client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/add-items/",
            queue.id,
          ),
          payload,
        );
        assert(
          Number(added?.added) === 1 &&
            Number(added?.total_matching || 0) === 1,
          `${spec.key} filter-mode add returned unexpected result: ${JSON.stringify(
            added,
          )}.`,
        );

        const entry = await findQueueEntryForSource(
          client,
          queue.id,
          spec.sourceType,
          spec.sourceId,
        );
        assert(
          entry?.item?.id &&
            entry.item.source_type === spec.sourceType &&
            String(entry.item.source_id) === String(spec.sourceId),
          `${spec.key} for-source did not reload the filter-created item.`,
        );
        const itemId = entry.item.id;
        created.push({ ...spec, queueId: queue.id, itemId });
        cleanup.defer(`delete ${spec.key} filter-mode queue item`, () =>
          deleteQueueItemIfPresent(client, queue.id, itemId),
        );

        const duplicate = await client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/add-items/",
            queue.id,
          ),
          payload,
        );
        assert(
          Number(duplicate?.added) === 0 &&
            Number(duplicate?.duplicates || 0) === 1,
          `${spec.key} filter-mode duplicate guard returned unexpected result: ${JSON.stringify(
            duplicate,
          )}.`,
        );

        const listedItems = asArray(
          await client.get(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/items/",
              queue.id,
            ),
            {
              query: {
                limit: 100,
                source_type: spec.listSourceTypes,
                status: ["pending", "in_progress", "completed", "skipped"],
              },
            },
          ),
        );
        assert(
          listedItems.some(
            (item) =>
              String(item.id) === String(itemId) &&
              item.source_type === spec.sourceType,
          ),
          `${spec.key} filter-created item was not visible in the filtered queue list.`,
        );

        const detail = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
            queue.id,
            { id: itemId },
          ),
          { query: { include_completed: true, include_all_annotations: true } },
        );
        assert(
          detail?.item?.id === itemId &&
            detail.item.source_type === spec.sourceType,
          `${spec.key} annotate-detail did not reload the filter-created item.`,
        );

        evidence.push({
          endpoint: "annotation queue observe filter-mode add",
          key: spec.key,
          source_type: spec.sourceType,
          source_id: spec.sourceId,
          project_id: spec.projectId,
          queue_id: queue.id,
          queue_item_id: itemId,
          total_matching: added.total_matching,
          duplicate_count: duplicate.duplicates,
        });
      }

      evidence.push({
        filter_mode_created_item_ids: created.map((item) => item.itemId),
        filter_mode_source_types: created.map((item) => item.sourceType),
      });
    },
  },
  {
    id: "AQ-API-022",
    title:
      "Import annotations writes scoped scores and updates without duplicates",
    tags: ["annotation", "mutating", "import", "scores", "data-roundtrip"],
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
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const labelName = `api journey import text ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for queue import journey.",
          settings: {
            placeholder: "Queue import journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : label?.label?.id
          ? label.label
          : await findAnnotationLabelByName(client, labelName);
      assert(
        createdLabel?.id,
        "Import journey label create did not return id.",
      );
      cleanup.defer("delete import journey label", () =>
        deleteAnnotationLabelIfPresent(client, createdLabel.id),
      );

      const unattachedName = `api journey import unattached ${runId}`;
      const unattached = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: unattachedName,
          type: "text",
          description: "Disposable unattached label for import guard.",
          settings: {
            placeholder: "Should not import",
            min_length: 0,
            max_length: 500,
          },
        },
      );
      const unattachedLabel = unattached?.id
        ? unattached
        : unattached?.label?.id
          ? unattached.label
          : await findAnnotationLabelByName(client, unattachedName);
      assert(
        unattachedLabel?.id,
        "Unattached import guard label create did not return id.",
      );
      cleanup.defer("delete unattached import guard label", () =>
        deleteAnnotationLabelIfPresent(client, unattachedLabel.id),
      );

      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        sample.traceId,
        sample.projectId,
        evidence,
      );
      await client.post(
        apiPath("/model-hub/annotation-queues/{id}/add-label/", {
          id: queue.id,
        }),
        { label_id: createdLabel.id, required: false },
      );
      cleanup.defer("remove import journey label from queue", () =>
        removeQueueLabelIfPresent(client, queue.id, createdLabel.id),
      );

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [{ source_type: "trace", source_id: sample.traceId }],
        },
      );
      assert(
        Number(added?.added) === 1 && asArray(added?.errors).length === 0,
        `Import journey add-items returned unexpected result: ${JSON.stringify(
          added,
        )}.`,
      );

      const entry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        sample.traceId,
      );
      assert(
        entry?.item?.id &&
          entry.item.source_type === "trace" &&
          String(entry.item.source_id) === String(sample.traceId),
        "Import journey for-source did not reload the trace queue item.",
      );
      const itemId = entry.item.id;
      cleanup.defer("delete import journey queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, itemId),
      );

      const firstValue = { text: `imported first ${runId}` };
      const firstNote = `import first note ${runId}`;
      const imported = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/import/",
          queue.id,
          { id: itemId },
        ),
        {
          annotations: [
            {
              label_id: createdLabel.id,
              value: firstValue,
              notes: firstNote,
              score_source: "imported",
            },
          ],
        },
      );
      assert(
        Number(imported?.imported) === 1,
        `Import annotations expected imported=1, saw ${JSON.stringify(imported)}.`,
      );

      const secondValue = { text: `imported updated ${runId}` };
      const secondNote = `import updated note ${runId}`;
      const updatedImport = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/import/",
          queue.id,
          { id: itemId },
        ),
        {
          annotations: [
            {
              label_id: createdLabel.id,
              value: secondValue,
              notes: secondNote,
              score_source: "imported",
            },
          ],
        },
      );
      assert(
        Number(updatedImport?.imported) === 1,
        `Second import expected imported=1, saw ${JSON.stringify(updatedImport)}.`,
      );

      const ignoredImport = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/import/",
          queue.id,
          { id: itemId },
        ),
        {
          annotations: [
            {
              label_id: unattachedLabel.id,
              value: { text: `unattached ${runId}` },
              score_source: "imported",
            },
          ],
        },
      );
      assert(
        Number(ignoredImport?.imported) === 0,
        `Unattached label import should be ignored, saw ${JSON.stringify(
          ignoredImport,
        )}.`,
      );

      const annotations = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
            queue.id,
            { id: itemId },
          ),
        ),
      );
      const importedScores = annotations.filter(
        (score) =>
          String(score.label_id) === String(createdLabel.id) &&
          String(score.queue_item) === String(itemId) &&
          String(score.annotator) === String(userId),
      );
      assert(
        importedScores.length === 1,
        `Import should leave exactly one active score for the attached label, saw ${importedScores.length}.`,
      );
      const importedScore = importedScores[0];
      assert(
        importedScore.source_type === "trace" &&
          String(importedScore.source_id) === String(sample.traceId) &&
          String(importedScore.queue_id) === String(queue.id),
        "Imported score did not preserve trace source and queue context.",
      );
      assert(
        sameJsonValue(importedScore.value, secondValue) &&
          importedScore.score_source === "imported" &&
          String(importedScore.notes || "").includes(secondNote),
        "Imported score did not expose the updated value, source, and notes.",
      );
      assert(
        asArray(importedScore.value_history).some((entryValue) =>
          sameJsonValue(entryValue?.value, firstValue),
        ),
        "Imported score did not retain the first value in value_history after update.",
      );
      assert(
        !annotations.some(
          (score) => String(score.label_id) === String(unattachedLabel.id),
        ),
        "Unattached label import leaked into the queue item annotations list.",
      );

      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: itemId },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        asArray(detail.annotations).some(
          (score) =>
            String(score.id) === String(importedScore.id) &&
            sameJsonValue(score.value, secondValue) &&
            String(score.notes || "").includes(secondNote),
        ),
        "Annotate detail did not reload the imported score.",
      );

      evidence.push({
        queue_id: queue.id,
        queue_item_id: itemId,
        trace_id: sample.traceId,
        trace_project_id: sample.projectId,
        label_id: createdLabel.id,
        unattached_label_id: unattachedLabel.id,
        score_id: importedScore.id,
        annotator_id: userId,
        organization_id: organizationId,
        workspace_id: workspaceId,
        value_history_count: asArray(importedScore.value_history).length,
        ignored_unattached_imported: ignoredImport.imported,
      });
    },
  },
  {
    id: "AQ-API-023",
    title: "Default queue source lookup and direct score annotation round-trip",
    tags: [
      "annotation",
      "mutating",
      "default-queue",
      "scores",
      "data-roundtrip",
    ],
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
      const userId = assertCurrentUserResolved(user);
      const seedSample = await resolveTraceAndSpanSample(client);
      const {
        queue: defaultQueue,
        source: defaultSource,
        action: defaultQueueAction,
      } = await resolveDefaultQueueForDirectTraceAnnotation(
        client,
        seedSample,
        evidence,
      );
      assert(defaultQueue?.id, "Default queue lookup did not return queue id.");

      const labelName = `api journey default queue direct ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description:
            "Disposable label for default queue direct score journey.",
          settings: {
            placeholder: "Default queue direct score journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : label?.label?.id
          ? label.label
          : await findAnnotationLabelByName(client, labelName);
      assert(
        createdLabel?.id,
        "Default queue journey label create did not return id.",
      );
      cleanup.defer("delete default queue direct label", () =>
        deleteAnnotationLabelIfPresent(client, createdLabel.id),
      );

      const addedLabel = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/add-label/", {
          id: defaultQueue.id,
        }),
        { label_id: createdLabel.id, required: false },
      );
      assert(
        String(addedLabel?.label?.id) === String(createdLabel.id),
        "Default queue add-label did not return the temporary label.",
      );
      cleanup.defer("remove default queue direct label from queue", () =>
        removeQueueLabelIfPresent(client, defaultQueue.id, createdLabel.id),
      );

      const defaultQueueFetch = await client.post(
        apiPath("/model-hub/annotation-queues/get-or-create-default/"),
        { project_id: defaultSource.projectId },
      );
      assert(
        String(defaultQueueFetch?.queue?.id) === String(defaultQueue.id),
        "get-or-create-default did not return the selected project default queue.",
      );
      assert(
        asArray(defaultQueueFetch?.labels).some(
          (row) => String(row.id) === String(createdLabel.id),
        ),
        "get-or-create-default did not return the temporary default queue label.",
      );

      const sample = await resolveDefaultQueueTraceSource(
        client,
        defaultQueue.id,
        defaultSource,
        evidence,
      );
      const beforeEntry = sample.entry;
      assert(
        beforeEntry?.queue?.is_default === true,
        "Default queue for-source entry was not marked is_default.",
      );
      assert(
        asArray(beforeEntry.labels).some(
          (row) => String(row.id) === String(createdLabel.id),
        ),
        "Default queue for-source lookup did not expose the temporary label before scoring.",
      );

      const preexistingQueueItemId = beforeEntry?.item?.id || null;
      const scoreValue = { text: `default queue direct score ${runId}` };
      const labelNote = `default queue label note ${runId}`;
      const created = await client.post(apiPath("/model-hub/scores/bulk/"), {
        source_type: "trace",
        source_id: sample.traceId,
        scores: [
          {
            label_id: createdLabel.id,
            value: scoreValue,
            notes: labelNote,
          },
        ],
      });
      const createdScores = asArray(created?.scores);
      assert(
        asArray(created?.errors).length === 0,
        `Default queue direct score returned errors: ${JSON.stringify(created?.errors)}.`,
      );
      assert(
        createdScores.length === 1,
        `Default queue direct score expected one score, saw ${createdScores.length}.`,
      );
      const createdScore = createdScores[0];
      const queueItemId = createdScore.queue_item || createdScore.queue_item_id;
      assert(
        queueItemId,
        "Default queue direct score did not attach to a queue item.",
      );
      assert(
        createdScore.source_type === "trace" &&
          String(createdScore.source_id) === String(sample.traceId) &&
          sameJsonValue(createdScore.value, scoreValue) &&
          createdScore.notes === labelNote,
        "Default queue direct score response did not preserve source, value, and label note.",
      );

      cleanup.defer("delete default queue direct score artifacts", async () => {
        await deleteScoreIfPresent(client, createdScore.id);
        if (!preexistingQueueItemId) {
          await deleteQueueItemIfPresent(client, defaultQueue.id, queueItemId);
        }
      });

      const afterEntry = await findDefaultQueueEntryForSource(
        client,
        defaultQueue.id,
        "trace",
        sample.traceId,
      );
      assert(
        String(afterEntry?.item?.id) === String(queueItemId),
        "Default queue source lookup did not reload the created queue item.",
      );
      assert(
        afterEntry.item.source_type === "trace" &&
          String(afterEntry.item.source_id) === String(sample.traceId),
        "Default queue item did not preserve trace source identity.",
      );
      assert(
        sameJsonValue(
          afterEntry.existing_scores?.[createdLabel.id],
          scoreValue,
        ),
        "Default queue source lookup did not prefill the saved direct score value.",
      );
      assert(
        afterEntry.existing_label_notes?.[createdLabel.id] === labelNote,
        "Default queue source lookup did not prefill the direct score label note.",
      );

      const itemAnnotations = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
            defaultQueue.id,
            { id: queueItemId },
          ),
        ),
      );
      assert(
        itemAnnotations.some(
          (score) =>
            String(score.id) === String(createdScore.id) &&
            String(score.queue_item) === String(queueItemId) &&
            String(score.label_id) === String(createdLabel.id) &&
            sameJsonValue(score.value, scoreValue) &&
            score.notes === labelNote,
        ),
        "Default queue item annotations did not include the direct score.",
      );

      const annotateDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          defaultQueue.id,
          { id: queueItemId },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        asArray(annotateDetail.labels).some(
          (row) => String(labelId(row)) === String(createdLabel.id),
        ),
        "Annotate detail did not include the default queue temporary label.",
      );
      assert(
        asArray(annotateDetail.annotations).some(
          (score) =>
            String(score.id) === String(createdScore.id) &&
            sameJsonValue(score.value, scoreValue) &&
            score.notes === labelNote,
        ),
        "Annotate detail did not reload the default queue direct score.",
      );

      const sourceScores = asArray(
        await client.get(apiPath("/model-hub/scores/for-source/"), {
          query: { source_type: "trace", source_id: sample.traceId },
        }),
      );
      assert(
        sourceScores.some(
          (score) =>
            String(score.id) === String(createdScore.id) &&
            String(score.queue_item) === String(queueItemId) &&
            String(score.queue_id) === String(defaultQueue.id),
        ),
        "Scores for-source did not include the default queue direct score.",
      );

      evidence.push({
        queue_id: defaultQueue.id,
        queue_action: defaultQueueAction,
        queue_item_id: queueItemId,
        preexisting_queue_item_id: preexistingQueueItemId,
        trace_id: sample.traceId,
        trace_project_id: sample.projectId,
        label_id: createdLabel.id,
        score_id: createdScore.id,
        annotator_id: userId,
        organization_id: organizationId,
        workspace_id: workspaceId,
        get_or_create_default_action: defaultQueueFetch?.action || null,
        default_source_had_item_before_score: Boolean(preexistingQueueItemId),
      });
    },
  },
  {
    id: "AQ-API-024",
    title: "Progress, analytics, and agreement metrics match database formulas",
    tags: ["annotation", "safe", "metrics", "db-audit"],
    async run({ client, user, organizationId, workspaceId, evidence }) {
      const userId = assertCurrentUserResolved(user);
      let resolved = null;
      let cleanupResult = null;

      try {
        resolved = await resolveMetricsQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          evidence,
        );
        const { queue, candidate } = resolved;

        const dbAudit = await loadQueueMetricsDbAudit(queue.id, userId);
        const expectedProgress = expectedProgressFromDb(dbAudit, userId);
        const progress = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/progress/", {
            id: queue.id,
          }),
        );
        assertProgressMatches(progress, expectedProgress);

        const expectedAnalytics = expectedAnalyticsFromDb(dbAudit);
        const analytics = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/analytics/", {
            id: queue.id,
          }),
        );
        assertAnalyticsMatches(analytics, expectedAnalytics);

        let agreementStatus = "verified";
        let expectedAgreement = null;
        try {
          const agreement = await client.get(
            apiPath("/model-hub/annotation-queues/{id}/agreement/", {
              id: queue.id,
            }),
          );
          expectedAgreement = expectedAgreementFromDb(dbAudit);
          assertAgreementMatches(agreement, expectedAgreement);
        } catch (error) {
          if (![402, 403].includes(error.status)) throw error;
          agreementStatus = `entitlement blocked (${error.status})`;
        }

        if (candidate?.seeded) {
          cleanupResult = await deleteMetricsQueueFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          selected_active_items:
            candidate?.active_items ?? dbAudit.items.length,
          selected_active_scores:
            candidate?.active_scores ?? dbAudit.scores.length,
          selected_comparable_item_labels:
            candidate?.comparable_item_labels ??
            countComparableItemLabels(dbAudit.scores),
          progress_total: progress.total,
          progress_completed: progress.completed,
          progress_skipped: progress.skipped,
          analytics_total: analytics.total,
          analytics_status_breakdown: analytics.status_breakdown,
          analytics_label_count: Object.keys(analytics.label_distribution || {})
            .length,
          agreement_status: agreementStatus,
          agreement_overall: expectedAgreement?.overall_agreement ?? null,
          organization_id: organizationId,
          workspace_id: workspaceId,
          db_items_sampled: dbAudit.items.length,
          db_scores_sampled: dbAudit.scores.length,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteMetricsQueueFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            metrics_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-025",
    title: "Export fields catalog matches queue labels and source samples",
    tags: ["annotation", "safe", "export", "db-audit"],
    async run({ client, user, organizationId, workspaceId, evidence }) {
      const userId = assertCurrentUserResolved(user);
      let resolved = null;
      let cleanupResult = null;

      try {
        resolved = await resolveExportFieldsQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          evidence,
        );
        const { queue, candidate } = resolved;
        const dbAudit = await loadQueueExportFieldsDbAudit(queue.id);
        const payload = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/export-fields/", {
            id: queue.id,
          }),
        );
        assertExportFieldsCatalogMatches(payload, dbAudit);

        const fields = asArray(payload.fields);
        const defaultMapping = asArray(payload.default_mapping);
        const attrFields = fields.filter((field) =>
          String(field.id || "").startsWith("attr:"),
        );
        const evalFields = fields.filter((field) =>
          String(field.id || "").startsWith("eval:"),
        );

        if (candidate?.seeded) {
          cleanupResult = await deleteExportFieldsQueueFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          candidate_source_types: candidate?.source_type_list || null,
          db_sample_items: asArray(dbAudit.sample_items).length,
          db_labels: asArray(dbAudit.labels).length,
          field_count: fields.length,
          default_mapping_count: defaultMapping.length,
          label_field_count: fields.filter((field) =>
            String(field.id || "").startsWith("label:"),
          ).length,
          attr_field_count: attrFields.length,
          eval_field_count: evalFields.length,
          organization_id: organizationId,
          workspace_id: workspaceId,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteExportFieldsQueueFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            export_fields_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-026",
    title: "Queue status updates enforce transitions and persist to database",
    tags: ["annotation", "mutating", "queue", "db-audit"],
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
      const userId = assertCurrentUserResolved(user);
      let resolved = null;
      let cleanupResult = null;

      try {
        resolved = await resolveStatusTransitionQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          runId,
          evidence,
        );
        const { queue, candidate } = resolved;
        assert(
          queue?.status === "active",
          `AQ-API-026 needs an active queue, got ${queue?.status || "unknown"}.`,
        );

        const originalDb = await loadQueueStatusDbAudit(queue.id);
        assert(
          originalDb?.status === "active",
          `DB queue status must start active, got ${JSON.stringify(
            originalDb,
          )}.`,
        );
        assert(
          String(originalDb.organization_id) === String(organizationId) &&
            String(originalDb.workspace_id) === String(workspaceId),
          "DB queue org/workspace scope did not match the journey context.",
        );
        if (!candidate?.seeded) {
          cleanup.defer("restore queue status", () =>
            restoreQueueStatusIfNeeded(client, queue.id, originalDb.status),
          );
        }

        const invalid = await expectHttpError(
          () =>
            client.post(
              apiPath("/model-hub/annotation-queues/{id}/update-status/", {
                id: queue.id,
              }),
              { status: "draft" },
            ),
          400,
          "Cannot transition",
        );
        const afterInvalidDb = await loadQueueStatusDbAudit(queue.id);
        assert(
          afterInvalidDb.status === originalDb.status,
          `Invalid transition changed DB status: ${JSON.stringify(
            afterInvalidDb,
          )}.`,
        );

        const paused = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/update-status/", {
            id: queue.id,
          }),
          { status: "paused" },
        );
        assert(
          paused?.status === "paused",
          `Pause response had wrong status: ${JSON.stringify(paused)}.`,
        );
        const pausedDb = await loadQueueStatusDbAudit(queue.id);
        assert(
          pausedDb.status === "paused",
          `Pause did not persist to DB: ${JSON.stringify(pausedDb)}.`,
        );
        const pausedDetail = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
        );
        assert(
          pausedDetail?.status === "paused",
          `Paused queue detail had wrong status: ${JSON.stringify(
            pausedDetail,
          )}.`,
        );

        const restored = await client.post(
          apiPath("/model-hub/annotation-queues/{id}/update-status/", {
            id: queue.id,
          }),
          { status: originalDb.status },
        );
        assert(
          restored?.status === originalDb.status,
          `Restore response had wrong status: ${JSON.stringify(restored)}.`,
        );
        const restoredDb = await loadQueueStatusDbAudit(queue.id);
        assert(
          restoredDb.status === originalDb.status,
          `Restore did not persist to DB: ${JSON.stringify(restoredDb)}.`,
        );
        const restoredDetail = await client.get(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
        );
        assert(
          restoredDetail?.status === originalDb.status,
          `Restored queue detail had wrong status: ${JSON.stringify(
            restoredDetail,
          )}.`,
        );

        if (candidate?.seeded) {
          cleanupResult = await deleteStatusTransitionQueueFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          original_status: originalDb.status,
          invalid_transition_status: invalid.status,
          invalid_transition_body: invalid.body,
          status_after_invalid: afterInvalidDb.status,
          paused_api_status: paused.status,
          paused_db_status: pausedDb.status,
          paused_detail_status: pausedDetail.status,
          restored_api_status: restored.status,
          restored_db_status: restoredDb.status,
          restored_detail_status: restoredDetail.status,
          updated_at_changed_on_pause:
            String(pausedDb.updated_at || "") !==
            String(originalDb.updated_at || ""),
          organization_id: organizationId,
          workspace_id: workspaceId,
          db_organization_id: restoredDb.organization_id,
          db_workspace_id: restoredDb.workspace_id,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteStatusTransitionQueueFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            status_transition_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-027",
    title: "Queue full update preserves settings and bindings in database",
    tags: ["annotation", "mutating", "queue", "db-audit"],
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
      const userId = assertCurrentUserResolved(user);
      let resolved = null;
      let cleanupResult = null;

      try {
        resolved = await resolveQueueFullUpdateQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          runId,
          evidence,
        );
        const { queue, candidate } = resolved;
        const originalPayload = buildQueuePutPayload(queue);
        if (
          candidate?.seeded &&
          !originalPayload.annotator_ids.some(
            (annotatorId) => String(annotatorId) === String(userId),
          )
        ) {
          originalPayload.annotator_ids.push(userId);
          originalPayload.annotator_roles[String(userId)] = [
            "manager",
            "reviewer",
            "annotator",
          ];
        }
        const originalDb = await loadQueueStatusDbAudit(queue.id);
        assert(
          String(originalDb.organization_id) === String(organizationId) &&
            String(originalDb.workspace_id) === String(workspaceId),
          "DB queue org/workspace scope did not match the journey context.",
        );
        if (!candidate?.seeded) {
          cleanup.defer("restore queue full-update settings", () =>
            putQueueSettings(client, queue.id, originalPayload),
          );
        }

        const updatedDescription = `api journey full PUT ${runId}`;
        const updatedTimeout =
          Number(originalPayload.reservation_timeout_minutes || 60) === 60
            ? 45
            : 60;
        const updatePayload = {
          ...originalPayload,
          description: updatedDescription,
          reservation_timeout_minutes: updatedTimeout,
        };

        const updated = await client.put(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
          updatePayload,
        );
        assert(
          updated?.description === updatedDescription &&
            Number(updated.reservation_timeout_minutes) === updatedTimeout,
          `Queue PUT response did not include updated settings: ${JSON.stringify(
            updated,
          )}.`,
        );

        const updatedDb = await loadQueueStatusDbAudit(queue.id);
        assertQueueSettingsDbState(updatedDb, {
          organizationId,
          workspaceId,
          description: updatedDescription,
          reservationTimeout: updatedTimeout,
          activeLabelCount: originalDb.active_label_count,
          activeAnnotatorCount: originalDb.active_annotator_count,
        });

        const restored = await client.put(
          apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
          originalPayload,
        );
        assert(
          restored?.description === originalPayload.description &&
            Number(restored.reservation_timeout_minutes) ===
              Number(originalPayload.reservation_timeout_minutes),
          `Queue PUT restore response had wrong settings: ${JSON.stringify(
            restored,
          )}.`,
        );
        const restoredDb = await loadQueueStatusDbAudit(queue.id);
        assertQueueSettingsDbState(restoredDb, {
          organizationId,
          workspaceId,
          description: originalDb.description,
          reservationTimeout: originalDb.reservation_timeout_minutes,
          activeLabelCount: originalDb.active_label_count,
          activeAnnotatorCount: originalDb.active_annotator_count,
        });

        if (candidate?.seeded) {
          cleanupResult = await deleteQueueFullUpdateFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          original_description: originalDb.description,
          updated_description: updatedDb.description,
          restored_description: restoredDb.description,
          original_reservation_timeout: originalDb.reservation_timeout_minutes,
          updated_reservation_timeout: updatedDb.reservation_timeout_minutes,
          restored_reservation_timeout: restoredDb.reservation_timeout_minutes,
          active_label_count: restoredDb.active_label_count,
          active_annotator_count: restoredDb.active_annotator_count,
          organization_id: organizationId,
          workspace_id: workspaceId,
          db_organization_id: restoredDb.organization_id,
          db_workspace_id: restoredDb.workspace_id,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteQueueFullUpdateFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            full_update_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-028",
    title: "Queue item direct create, full update, partial update, and cleanup",
    tags: ["annotation", "mutating", "items", "db-audit"],
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
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client, {
        cleanup,
        organizationId,
        workspaceId,
        userId,
        runId,
        evidence,
      });
      let resolved = null;
      let created = null;
      let itemDeleted = false;
      let cleanupResult = null;

      try {
        resolved = await resolveQueueItemCrudQueue(
          client,
          organizationId,
          workspaceId,
          userId,
          runId,
          evidence,
        );
        const { queue, candidate } = resolved;
        const createPayload = {
          source_type: "trace",
          source_id: sample.traceId,
          status: "pending",
          priority: 3,
          order: 7301,
          metadata: { api_journey: runId, stage: "created" },
        };
        created = await client.post(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          createPayload,
        );
        assert(created?.id, "Queue item direct create did not return id.");
        if (!candidate?.seeded) {
          cleanup.defer("delete direct CRUD queue item", () =>
            deleteQueueItemIfPresent(client, queue.id, created.id),
          );
        }
        assert(
          created.source_type === "trace" &&
            Number(created.priority) === createPayload.priority,
          `Queue item create response had wrong shape: ${JSON.stringify(
            created,
          )}.`,
        );
        const createdDb = await loadQueueItemDbAudit(created.id);
        assertQueueItemDbState(createdDb, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: "trace",
          traceId: sample.traceId,
          status: "pending",
          priority: createPayload.priority,
          order: createPayload.order,
          metadataStage: "created",
          deleted: false,
        });

        const putPayload = {
          source_type: "trace",
          status: "pending",
          priority: 5,
          order: 7302,
          metadata: {
            api_journey: runId,
            stage: "put",
            nested: { mode: "full" },
          },
        };
        const putUpdated = await client.put(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: created.id },
          ),
          putPayload,
        );
        assert(
          Number(putUpdated.priority) === putPayload.priority &&
            putUpdated.metadata?.stage === "put",
          `Queue item PUT response had wrong shape: ${JSON.stringify(
            putUpdated,
          )}.`,
        );
        const putDb = await loadQueueItemDbAudit(created.id);
        assertQueueItemDbState(putDb, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: "trace",
          traceId: sample.traceId,
          status: "pending",
          priority: putPayload.priority,
          order: putPayload.order,
          metadataStage: "put",
          deleted: false,
        });

        const patchPayload = {
          priority: 8,
          metadata: {
            api_journey: runId,
            stage: "patch",
            nested: { mode: "partial" },
          },
        };
        const patched = await client.patch(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: created.id },
          ),
          patchPayload,
        );
        assert(
          Number(patched.priority) === patchPayload.priority &&
            patched.metadata?.stage === "patch",
          `Queue item PATCH response had wrong shape: ${JSON.stringify(
            patched,
          )}.`,
        );
        const detail = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/",
            queue.id,
            { id: created.id },
          ),
        );
        assert(
          detail?.id === created.id &&
            detail.metadata?.stage === "patch" &&
            detail.source_type === "trace",
          `Queue item detail after PATCH had wrong shape: ${JSON.stringify(
            detail,
          )}.`,
        );
        const patchedDb = await loadQueueItemDbAudit(created.id);
        assertQueueItemDbState(patchedDb, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: "trace",
          traceId: sample.traceId,
          status: "pending",
          priority: patchPayload.priority,
          order: putPayload.order,
          metadataStage: "patch",
          deleted: false,
        });

        await deleteQueueItemIfPresent(client, queue.id, created.id);
        itemDeleted = true;
        const cleanupDb = await loadQueueItemDbAudit(created.id);
        assertQueueItemDbState(cleanupDb, {
          queueId: queue.id,
          organizationId,
          workspaceId,
          sourceType: "trace",
          traceId: sample.traceId,
          status: "pending",
          priority: patchPayload.priority,
          order: putPayload.order,
          metadataStage: "patch",
          deleted: true,
        });

        if (candidate?.seeded) {
          cleanupResult = await deleteQueueItemCrudFixtureDb(queue.id);
        }

        evidence.push({
          queue_id: queue.id,
          queue_name: queue.name,
          queue_item_id: created.id,
          trace_id: sample.traceId,
          trace_project_id: sample.projectId,
          created_priority: createdDb.priority,
          put_priority: putDb.priority,
          patched_priority: patchedDb.priority,
          metadata_stage_after_patch: patchedDb.metadata?.stage,
          cleanup_deleted: cleanupDb.deleted,
          cleanup_active_child_rows: cleanupDb.active_child_rows,
          organization_id: organizationId,
          workspace_id: workspaceId,
          seeded_fixture: Boolean(candidate?.seeded),
          cleanup: cleanupResult ? [cleanupResult] : [],
        });
      } finally {
        if (resolved?.queue?.id && created?.id && !itemDeleted) {
          await deleteQueueItemIfPresent(client, resolved.queue.id, created.id);
        }
        if (resolved?.candidate?.seeded && !cleanupResult) {
          const fallbackCleanup = await deleteQueueItemCrudFixtureDb(
            resolved.queue.id,
          );
          evidence.push({
            item_crud_fixture_cleanup: fallbackCleanup,
          });
        }
      }
    },
  },
  {
    id: "AQ-API-006",
    title: "Submit annotations and whole-item notes round-trip",
    tags: ["annotation", "mutating", "submit"],
    async run({ client, runId, evidence }) {
      requireMutations();
      const queue = await resolveQueue(client, evidence);
      const item = await resolveQueueItem(client, queue.id, evidence, {
        status: ["pending", "in_progress", "skipped", "completed"],
      });
      const { detail, labels } = await getQueueLabels(
        client,
        queue.id,
        item.id,
      );
      if (!labels.length) skip("Queue item has no labels to submit.");

      const annotations = labels.map((label) => ({
        label_id: label.label_id || label.id,
        value: annotationValueForLabel(label),
        notes: label.allow_notes ? `label note ${runId}` : "",
      }));
      const itemNotes = `whole item note ${runId}`;
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
          queue.id,
          { id: item.id },
        ),
        { annotations, item_notes: itemNotes },
      );

      const updated = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        {
          query: {
            include_completed: true,
            include_all_annotations: true,
          },
        },
      );
      assert(
        asArray(updated.annotations).length >=
          asArray(detail.annotations).length,
        "Submit did not leave annotations visible in annotate detail.",
      );
      assert(
        String(updated.existing_notes || "").includes(itemNotes),
        "Whole-item notes did not round-trip through annotate detail.",
      );

      const savedScores = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
            queue.id,
            { id: item.id },
          ),
        ),
      );
      for (const expected of annotations) {
        assert(
          savedScores.some(
            (score) =>
              String(score.label_id) === String(expected.label_id) &&
              sameJsonValue(score.value, expected.value),
          ),
          `Submitted annotation for label ${expected.label_id} did not round-trip through the item annotations API.`,
        );
      }

      evidence.push({
        item_id: item.id,
        submitted_labels: annotations.length,
        item_notes: itemNotes,
      });
    },
  },
  {
    id: "AQ-API-007",
    title: "Automation rule CRUD and preview uses canonical filter shape",
    tags: ["annotation", "mutating", "automation", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const queue = await resolveManagerQueue(client, evidence);
      const rule = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/",
          queue.id,
        ),
        {
          name: `api journey rule ${runId}`,
          source_type: "trace",
          conditions: {
            operator: "and",
            filter: [
              {
                column_id: "status",
                filter_config: {
                  filter_type: "text",
                  filter_op: "equals",
                  filter_value: "OK",
                },
              },
            ],
          },
          enabled: true,
          trigger_frequency: "manual",
        },
      );
      assert(rule?.id, "Automation rule create did not return id.");
      cleanup.defer("delete automation rule", () =>
        deleteAutomationRuleIfPresent(client, queue.id, rule.id),
      );

      const createdDb = await loadAutomationRuleDbAudit(rule.id);
      assertAutomationRuleDbState(createdDb, {
        name: `api journey rule ${runId}`,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: true,
        triggerFrequency: "manual",
        firstFilterColumn: "status",
        deleted: false,
      });

      const listAfterCreate = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/automation-rules/",
            queue.id,
          ),
        ),
      );
      assert(
        listAfterCreate.some((row) => String(row.id) === String(rule.id)),
        "Automation rule list did not include the created rule.",
      );

      const read = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
          queue.id,
          { id: rule.id },
        ),
      );
      assert(
        read?.id === rule.id &&
          read?.conditions?.filter?.[0]?.column_id === "status",
        `Automation rule read had wrong shape: ${JSON.stringify(read)}.`,
      );

      const putName = `api journey rule ${runId} put`;
      const putUpdated = await client.put(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
          queue.id,
          { id: rule.id },
        ),
        {
          name: putName,
          source_type: "trace",
          conditions: {
            operator: "and",
            filter: [
              {
                column_id: "status",
                filter_config: {
                  filter_type: "text",
                  filter_op: "equals",
                  filter_value: "OK",
                },
              },
            ],
          },
          enabled: false,
          trigger_frequency: "manual",
        },
      );
      assert(
        putUpdated?.name === putName && putUpdated.enabled === false,
        `Automation rule PUT did not persist full update: ${JSON.stringify(
          putUpdated,
        )}.`,
      );
      assertAutomationRuleDbState(await loadAutomationRuleDbAudit(rule.id), {
        name: putName,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: false,
        triggerFrequency: "manual",
        firstFilterColumn: "status",
        deleted: false,
      });

      const patchName = `api journey rule ${runId} patched`;
      const updated = await client.patch(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
          queue.id,
          { id: rule.id },
        ),
        {
          name: patchName,
          source_type: "trace",
          conditions: {
            operator: "and",
            filter: [
              {
                column_id: "latency",
                filter_config: {
                  filter_type: "number",
                  filter_op: "greater_than_or_equal",
                  filter_value: 0,
                },
              },
            ],
          },
          enabled: true,
          trigger_frequency: "manual",
        },
      );
      assert(
        updated?.conditions?.filter?.[0]?.column_id === "latency",
        "Automation rule update did not preserve canonical filters.",
      );
      const patchedDb = await loadAutomationRuleDbAudit(rule.id);
      assertAutomationRuleDbState(patchedDb, {
        name: patchName,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: true,
        triggerFrequency: "manual",
        firstFilterColumn: "latency",
        deleted: false,
      });

      if (envFlag("API_JOURNEY_AUTOMATION_PREVIEW")) {
        const preview = await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/preview/",
            queue.id,
            { id: rule.id },
          ),
        );
        assert(
          typeof preview.matched === "number",
          "Automation preview did not include matched count.",
        );
        evidence.push({ preview_matched: preview.matched });
      }

      await client.delete(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
          queue.id,
          { id: rule.id },
        ),
      );
      await expectHttpStatus(
        () =>
          client.get(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
              queue.id,
              { id: rule.id },
            ),
          ),
        404,
      );
      const deletedDb = await loadAutomationRuleDbAudit(rule.id);
      assertAutomationRuleDbState(deletedDb, {
        name: patchName,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: true,
        triggerFrequency: "manual",
        firstFilterColumn: "latency",
        deleted: true,
      });

      evidence.push({
        rule_id: rule.id,
        queue_id: queue.id,
        queue_name: queue.name,
        list_count_after_create: listAfterCreate.length,
        read_filter_column: read.conditions?.filter?.[0]?.column_id,
        put_enabled: putUpdated.enabled,
        patch_filter_column: updated.conditions?.filter?.[0]?.column_id,
        db_deleted: deletedDb.deleted,
        db_deleted_at: deletedDb.deleted_at,
        organization_id: organizationId,
        workspace_id: workspaceId,
      });
    },
  },
  {
    id: "AQ-API-008",
    title:
      "Download JSON export returns source, evals, annotations, and review metadata",
    tags: ["annotation", "safe", "export", "db-audit"],
    async run({ client, evidence }) {
      const queue = await resolveQueue(client, evidence);
      const rows = await client.get(
        withQuery(
          apiPath("/model-hub/annotation-queues/{id}/export/", {
            id: queue.id,
          }),
          {
            export_format: "json",
          },
        ),
      );
      assert(Array.isArray(rows), "Queue JSON export must return an array.");
      if (rows.length) {
        const row = rows[0];
        for (const key of [
          "item_id",
          "source_type",
          "review",
          "annotations",
          "evals",
          "source",
        ]) {
          assert(
            Object.prototype.hasOwnProperty.call(row, key),
            `Queue JSON export row is missing ${key}.`,
          );
        }
        const dbAudit = await loadQueueExportRowDbAudit({
          queueId: queue.id,
          itemId: row.item_id,
        });
        assert(
          String(dbAudit.item?.id) === String(row.item_id) &&
            String(dbAudit.item?.queue_id) === String(queue.id) &&
            dbAudit.item?.source_type === row.source_type &&
            String(dbAudit.item?.source_id || "") ===
              String(row.source_id || "") &&
            dbAudit.item?.status === row.status &&
            Number(dbAudit.item?.order || 0) === Number(row.order || 0),
          `Queue export JSON row did not match DB item state: ${JSON.stringify({
            row,
            dbAudit,
          })}.`,
        );
        assert(
          String(row.review?.status || "") ===
            String(dbAudit.item?.review_status || "") &&
            String(row.review?.reviewed_by_id || "") ===
              String(dbAudit.item?.reviewed_by_id || "") &&
            String(row.review?.notes || "") ===
              String(dbAudit.item?.review_notes || ""),
          `Queue export review metadata did not match DB item state: ${JSON.stringify(
            { review: row.review, dbItem: dbAudit.item },
          )}.`,
        );
        assert(
          String(row.item_notes || "") ===
            String(dbAudit.item_note?.notes || "") &&
            asArray(row.annotations).length ===
              Number(dbAudit.active_score_count || 0),
          `Queue export annotation/note counts did not match DB state: ${JSON.stringify(
            { row, dbAudit },
          )}.`,
        );
        if (Number(dbAudit.active_score_count || 0) > 0) {
          const exportedScore = asArray(row.annotations).find(
            (score) =>
              String(score.label_id) === String(dbAudit.first_score?.label_id),
          );
          assert(
            exportedScore &&
              exportedScore.label_name === dbAudit.first_score?.label_name &&
              sameJsonValue(exportedScore.value, dbAudit.first_score?.value) &&
              String(exportedScore.notes || "") ===
                String(dbAudit.first_score?.notes || "") &&
              String(exportedScore.score_source || "") ===
                String(dbAudit.first_score?.score_source || ""),
            `Queue export first score did not match DB state: ${JSON.stringify({
              exportedScore,
              dbAudit,
            })}.`,
          );
        }
      }

      const csvExport = await client.get(
        withQuery(
          apiPath("/model-hub/annotation-queues/{id}/export/", {
            id: queue.id,
          }),
          {
            export_format: "csv",
          },
        ),
      );
      const csvText = String(csvExport || "");
      for (const header of [
        "item_id",
        "source_type",
        "review_status",
        "reviewer_email",
        "label_id",
        "value",
      ]) {
        assert(
          csvText.includes(header),
          `Queue CSV export missing ${header} header.`,
        );
      }
      if (rows.length) {
        assert(
          csvText.includes(String(rows[0].item_id)),
          "Queue CSV export did not include the first exported item id.",
        );
      }
      evidence.push({
        queue_id: queue.id,
        exported_rows: rows.length,
        csv_export_bytes: csvText.length,
      });
    },
  },
  {
    id: "AQ-API-016",
    title:
      "Automation rule preview and run creates one scoped queue item with cleanup",
    tags: ["annotation", "mutating", "automation", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueueWithoutSource(
        client,
        "trace",
        sample.traceId,
        sample.projectId,
        evidence,
      );

      const rule = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/",
          queue.id,
        ),
        {
          name: `api journey automation run ${runId}`,
          source_type: "trace",
          conditions: {
            rules: [
              {
                field: "trace_id",
                op: "equals",
                value: sample.traceId,
              },
            ],
          },
          enabled: true,
          trigger_frequency: "manual",
        },
      );
      assert(rule?.id, "Automation rule create did not return id.");
      cleanup.defer("delete automation run rule", () =>
        client.delete(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
            queue.id,
            { id: rule.id },
          ),
        ),
      );

      const preview = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/preview/",
          queue.id,
          { id: rule.id },
        ),
      );
      assert(
        Number(preview.matched) === 1 && Number(preview.added) === 0,
        `Automation preview mismatch: ${JSON.stringify(preview)}.`,
      );

      const evaluated = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/",
          queue.id,
          { id: rule.id },
        ),
      );
      assert(
        Number(evaluated.matched) === 1 && Number(evaluated.added) === 1,
        `Automation evaluate did not add exactly one item: ${JSON.stringify(
          evaluated,
        )}.`,
      );

      const entry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        sample.traceId,
      );
      assert(
        entry?.item?.id &&
          String(entry.item.source_id) === String(sample.traceId),
        "Automation run did not create a readable trace queue item.",
      );
      cleanup.defer("delete automation-created queue item", () =>
        deleteQueueItemIfPresent(client, queue.id, entry.item.id),
      );

      const createdDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/",
          queue.id,
          { id: entry.item.id },
        ),
      );
      assert(
        createdDetail?.source_type === "trace" &&
          String(createdDetail.queue) === String(queue.id) &&
          (!sample.projectId ||
            String(createdDetail.source_preview?.project_id) ===
              String(sample.projectId)),
        `Automation-created item has wrong source shape: ${JSON.stringify(
          createdDetail,
        )}.`,
      );

      const duplicateStatus = await expectHttpStatus(
        () =>
          client.post(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/",
              queue.id,
              { id: rule.id },
            ),
          ),
        409,
      );

      evidence.push({
        queue_id: queue.id,
        rule_id: rule.id,
        trace_id: sample.traceId,
        queue_item_id: entry.item.id,
        preview_matched: preview.matched,
        evaluate_added: evaluated.added,
        duplicate_run_status: duplicateStatus,
      });
    },
  },
  {
    id: "AQ-API-033",
    title:
      "Scheduled automation rule guardrails persist without queue mutation",
    tags: ["annotation", "mutating", "automation", "schedule", "db-audit"],
    async run({
      apiBase,
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const namePrefix = `api journey automation schedule ${runId}`;
      const queueName = `${namePrefix} queue`;
      cleanup.defer("hard-delete automation schedule DB fixtures", () =>
        deleteQueueCreateFixturesDb(namePrefix),
      );

      let queue;
      let createMode = "api";
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description:
            "Disposable queue for scheduled automation rule guardrail coverage.",
          instructions: "Verify scheduled automation persistence.",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: false,
          auto_assign: false,
        });
      } catch (error) {
        if (error.status !== 402) throw error;
        createMode = "db_seeded_after_create_entitlement";
        evidence.push({
          create_entitlement_status: error.status,
          create_entitlement_body: error.body,
        });
        queue = await insertAnnotationQueueCreateFixtureDb({
          queueName,
          organizationId,
          workspaceId,
          userId,
        });
      }
      assert(
        queue?.id,
        "Automation schedule queue create/seed returned no id.",
      );
      cleanup.defer("hard-delete automation schedule queue", () =>
        hardDeleteQueueIfPresent(client, queue.id, queueName),
      );

      const noMatchFilter = [
        {
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: `api-journey-no-match-${runId}`,
          },
        },
      ];
      let scheduledRule;
      try {
        scheduledRule = await client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/automation-rules/",
            queue.id,
          ),
          {
            name: `${namePrefix} daily rule`,
            source_type: "trace",
            conditions: {
              operator: "and",
              filter: noMatchFilter,
            },
            enabled: true,
            trigger_frequency: "daily",
          },
        );
      } catch (error) {
        const bodyText = JSON.stringify(error.body || {});
        if (
          error.status === 403 &&
          /automation rules limit|ENTITLEMENT_LIMIT/i.test(bodyText)
        ) {
          evidence.push({
            automation_rule_entitlement_status: error.status,
            automation_rule_entitlement_body: error.body,
          });
          skip(
            "Automation rule creation is entitlement-blocked in this workspace.",
          );
        }
        throw error;
      }
      assert(
        scheduledRule?.id,
        "Scheduled automation rule create returned no id.",
      );
      cleanup.defer("delete scheduled automation rule", () =>
        deleteAutomationRuleIfPresent(client, queue.id, scheduledRule.id),
      );
      assert(
        scheduledRule.trigger_frequency === "daily" &&
          Number(scheduledRule.trigger_count) === 0 &&
          scheduledRule.last_triggered_at == null,
        `Scheduled rule response had wrong frequency/counters: ${JSON.stringify(
          scheduledRule,
        )}.`,
      );

      const scheduledDb = await loadAutomationRuleDbAudit(scheduledRule.id);
      assertAutomationRuleDbState(scheduledDb, {
        name: `${namePrefix} daily rule`,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: true,
        triggerFrequency: "daily",
        firstFilterColumn: "status",
        deleted: false,
      });
      assert(
        Number(scheduledDb.trigger_count) === 0 &&
          scheduledDb.last_triggered_at == null,
        `Scheduled rule DB counters should be untouched before manual run: ${JSON.stringify(
          scheduledDb,
        )}.`,
      );

      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
          queue.id,
          { id: scheduledRule.id },
        ),
      );
      assert(
        detail?.trigger_frequency === "daily" &&
          detail?.conditions?.filter?.[0]?.filter_config?.filter_value ===
            `api-journey-no-match-${runId}`,
        `Scheduled rule detail did not round-trip canonical filter: ${JSON.stringify(
          detail,
        )}.`,
      );

      const list = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/automation-rules/",
            queue.id,
          ),
        ),
      );
      assert(
        list.some((row) => String(row.id) === String(scheduledRule.id)),
        "Scheduled rule list did not include the created rule.",
      );

      const preview = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/preview/",
          queue.id,
          { id: scheduledRule.id },
        ),
      );
      assert(
        Number(preview.matched) === 0 && Number(preview.added) === 0,
        `Scheduled rule no-match preview should not mutate queue items: ${JSON.stringify(
          preview,
        )}.`,
      );

      async function createInvalidRuleStatus(payload) {
        try {
          await client.post(
            queuePath(
              "/model-hub/annotation-queues/{queue_id}/automation-rules/",
              queue.id,
            ),
            payload,
          );
        } catch (error) {
          if (error.status === 400) return error.status;
          const bodyText = JSON.stringify(error.body || {});
          if (
            error.status === 403 &&
            /automation rules limit|ENTITLEMENT_LIMIT/i.test(bodyText)
          ) {
            return error.status;
          }
          throw error;
        }
        throw new Error("Expected invalid automation rule create to fail.");
      }

      const bothShapesStatus = await createInvalidRuleStatus({
        name: `${namePrefix} invalid both shapes`,
        source_type: "trace",
        conditions: {
          filter: noMatchFilter,
          rules: [{ field: "trace_id", op: "equals", value: randomUUID() }],
        },
        enabled: true,
        trigger_frequency: "hourly",
      });
      const legacyFiltersStatus = await createInvalidRuleStatus({
        name: `${namePrefix} invalid filters key`,
        source_type: "trace",
        conditions: { filters: noMatchFilter },
        enabled: true,
        trigger_frequency: "weekly",
      });
      const invalidLegacyFieldStatus = await createInvalidRuleStatus({
        name: `${namePrefix} invalid legacy field`,
        source_type: "trace",
        conditions: {
          rules: [
            {
              field: "totally_made_up_column",
              op: "equals",
              value: "x",
            },
          ],
        },
        enabled: true,
        trigger_frequency: "monthly",
      });

      let altGuard = { status: "skipped_no_alt_token" };
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (altToken) {
        const altClient = createApiClient({
          apiBase,
          accessToken: altToken,
          organizationId,
          workspaceId,
        });
        const altUser = await altClient.get(apiPath("/accounts/user-info/"));
        const altUserId = currentUserId(altUser);
        if (altUserId && String(altUserId) !== String(userId)) {
          altGuard = {
            user_id: altUserId,
            create_status: await expectHttpStatus(
              () =>
                altClient.post(
                  queuePath(
                    "/model-hub/annotation-queues/{queue_id}/automation-rules/",
                    queue.id,
                  ),
                  {
                    name: `${namePrefix} alt forbidden`,
                    source_type: "trace",
                    conditions: { filter: noMatchFilter },
                    enabled: true,
                    trigger_frequency: "daily",
                  },
                ),
              403,
            ),
            preview_status: await expectHttpStatus(
              () =>
                altClient.get(
                  queuePath(
                    "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/preview/",
                    queue.id,
                    { id: scheduledRule.id },
                  ),
                ),
              403,
            ),
            evaluate_status: await expectHttpStatus(
              () =>
                altClient.post(
                  queuePath(
                    "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/",
                    queue.id,
                    { id: scheduledRule.id },
                  ),
                ),
              403,
            ),
          };
        } else {
          altGuard = { status: "skipped_same_alt_user" };
        }
      }

      await deleteAutomationRuleIfPresent(client, queue.id, scheduledRule.id);
      const deletedDb = await loadAutomationRuleDbAudit(scheduledRule.id);
      assertAutomationRuleDbState(deletedDb, {
        name: `${namePrefix} daily rule`,
        queueId: queue.id,
        organizationId,
        workspaceId,
        sourceType: "trace",
        enabled: true,
        triggerFrequency: "daily",
        firstFilterColumn: "status",
        deleted: true,
      });

      await hardDeleteQueueIfPresent(client, queue.id, queueName);
      const finalResidue =
        await loadAutomationScheduleResidueDbAudit(namePrefix);
      assert(
        Number(finalResidue.matching_queues) === 0 &&
          Number(finalResidue.matching_rules) === 0 &&
          Number(finalResidue.matching_active_rules) === 0,
        `Automation schedule cleanup left DB residue: ${JSON.stringify(
          finalResidue,
        )}.`,
      );

      evidence.push({
        create_mode: createMode,
        queue_id: queue.id,
        rule_id: scheduledRule.id,
        trigger_frequency: scheduledRule.trigger_frequency,
        preview_matched: preview.matched,
        preview_added: preview.added,
        invalid_both_shapes_status: bothShapesStatus,
        invalid_filters_status: legacyFiltersStatus,
        invalid_legacy_field_status: invalidLegacyFieldStatus,
        alt_guard: altGuard,
        db_trigger_count: scheduledDb.trigger_count,
        db_last_triggered_at: scheduledDb.last_triggered_at,
        db_deleted_at: deletedDb.deleted_at,
        final_residue: finalResidue,
      });
    },
  },
  {
    id: "AQ-API-017",
    title: "Disposable queue archive, restore, and hard-delete lifecycle",
    tags: ["annotation", "mutating", "queue", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      let queueName = `api journey archive queue ${runId}`;
      let queue;
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description:
            "Created by API journey for archive/restore/hard-delete coverage.",
          instructions: "Disposable API journey queue.",
          assignment_strategy: "manual",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: false,
          auto_assign: false,
          label_ids: [],
        });
      } catch (error) {
        if (error.status !== 402) throw error;
        queue = await resolvePreseededDisposableArchiveQueue(client, evidence);
        queueName = queue.name;
      }
      assert(queue?.id, "Disposable archive queue create did not return id.");
      cleanup.defer("hard-delete archive journey queue", () =>
        hardDeleteQueueIfPresent(client, queue.id, queueName),
      );

      const updatedQueue = await client.patch(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
        {
          name: `${queueName} renamed`,
          description:
            "Renamed by API journey before archive/restore/hard-delete coverage.",
        },
      );
      assert(
        updatedQueue?.name === `${queueName} renamed`,
        `Queue PATCH response had wrong name: ${JSON.stringify(updatedQueue)}.`,
      );
      queueName = updatedQueue.name;

      await expectHttpStatus(
        () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
              id: queue.id,
            }),
            {},
          ),
        400,
      );
      await expectHttpStatus(
        () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
              id: queue.id,
            }),
            { force: true, confirm_name: `${queueName} wrong` },
          ),
        400,
      );

      const archived = await client.delete(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      assert(
        archived?.archived === true &&
          String(archived.queue_id) === String(queue.id),
        `Queue archive response had wrong shape: ${JSON.stringify(archived)}.`,
      );

      const activeAfterArchive = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { limit: 100, include_counts: true },
        }),
      );
      assert(
        !activeAfterArchive.some((row) => String(row.id) === String(queue.id)),
        "Archived queue still appeared in the active queue list.",
      );

      const archivedQueues = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { limit: 100, archived: true, include_counts: true },
        }),
      );
      assert(
        archivedQueues.some((row) => String(row.id) === String(queue.id)),
        "Archived queue did not appear in archived=true list.",
      );

      const restored = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/restore/", {
          id: queue.id,
        }),
        {},
      );
      assert(
        String(restored?.id) === String(queue.id) && restored.deleted !== true,
        `Queue restore response had wrong shape: ${JSON.stringify(restored)}.`,
      );

      const activeAfterRestore = asArray(
        await client.get(apiPath("/model-hub/annotation-queues/"), {
          query: { limit: 100, include_counts: true },
        }),
      );
      assert(
        activeAfterRestore.some((row) => String(row.id) === String(queue.id)),
        "Restored queue did not reappear in the active queue list.",
      );

      const hardDeleted = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
          id: queue.id,
        }),
        { force: true, confirm_name: queueName },
      );
      assert(
        hardDeleted?.hard_deleted === true &&
          String(hardDeleted.queue_id) === String(queue.id),
        `Queue hard-delete response had wrong shape: ${JSON.stringify(
          hardDeleted,
        )}.`,
      );

      await expectHttpStatus(
        () =>
          client.get(
            apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
          ),
        404,
      );

      evidence.push({
        queue_id: queue.id,
        archive_response: archived.archived,
        restore_deleted_flag: restored.deleted ?? false,
        hard_delete_response: hardDeleted.hard_deleted,
      });
    },
  },
  {
    id: "AQ-API-009",
    title:
      "Reviewer feedback persists per annotator and reloads for annotation UI",
    tags: ["annotation", "mutating", "review", "data-roundtrip"],
    async run({ client, user, runId, evidence }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const queue = await resolveQueue(client, evidence);
      const queueDetail = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      );
      const roles = asArray(queueDetail.viewer_roles);
      if (!roles.some((role) => ["manager", "reviewer"].includes(role))) {
        skip(
          "Current user is not a reviewer or manager for the selected queue.",
        );
      }

      const item = await resolveQueueItem(client, queue.id, evidence, {
        status: ["pending", "in_progress", "skipped", "completed"],
      });
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: [item.id],
          user_ids: [userId],
          action: "set",
        },
      );

      const { labels } = await getQueueLabels(client, queue.id, item.id);
      const label = labels.find((row) => row.label_id || row.id);
      if (!label)
        skip("Queue item has no label for reviewer feedback coverage.");
      const labelId = label.label_id || label.id;
      const labelValue = annotationValueForLabel(label);
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
          queue.id,
          { id: item.id },
        ),
        {
          annotations: [
            {
              label_id: labelId,
              value: labelValue,
              notes: label.allow_notes
                ? `review setup label note ${runId}`
                : "",
            },
          ],
        },
      );

      const savedScores = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/",
            queue.id,
            { id: item.id },
          ),
        ),
      );
      assert(
        savedScores.some(
          (score) =>
            String(score.label_id) === String(labelId) &&
            String(score.annotator) === String(userId),
        ),
        "Reviewer setup annotation was not saved for the current annotator.",
      );

      const overallFeedback = `api journey reviewer note ${runId}`;
      const labelFeedback = `api journey label feedback ${runId}`;
      const reviewed = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
          queue.id,
          { id: item.id },
        ),
        {
          action: "comment",
          notes: overallFeedback,
          label_comments: [
            {
              label_id: labelId,
              target_annotator_id: userId,
              comment: labelFeedback,
            },
          ],
        },
      );
      assert(
        asArray(reviewed.review_comments).some(
          (comment) => comment.comment === overallFeedback,
        ),
        "Review response did not include the overall feedback comment.",
      );
      assert(
        asArray(reviewed.review_comments).some(
          (comment) =>
            comment.comment === labelFeedback &&
            String(comment.label_id) === String(labelId) &&
            String(comment.target_annotator_id) === String(userId),
        ),
        "Review response did not include per-label feedback for the target annotator.",
      );

      const reloaded = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        {
          query: {
            include_completed: true,
            include_all_annotations: true,
          },
        },
      );
      const reloadedComments = asArray(reloaded.review_comments);
      assert(
        reloadedComments.some((comment) => comment.comment === overallFeedback),
        "Overall reviewer feedback did not reload through annotate detail.",
      );
      assert(
        reloadedComments.some(
          (comment) =>
            comment.comment === labelFeedback &&
            String(comment.label_id) === String(labelId) &&
            String(comment.target_annotator_id) === String(userId),
        ),
        "Per-label reviewer feedback did not reload with label and annotator identity.",
      );

      evidence.push({
        item_id: item.id,
        label_id: labelId,
        target_annotator_id: userId,
        review_comments: reloadedComments.length,
      });
    },
  },
  {
    id: "AQ-API-010",
    title:
      "Disposable queue annotate complete, next-item, skip, and progress round-trip",
    tags: ["annotation", "mutating", "submit", "navigation", "data-roundtrip"],
    async run({
      client,
      cleanup,
      user,
      runId,
      organizationId,
      workspaceId,
      evidence,
    }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const labelName = `api journey disposable text ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for queue navigation journey.",
          settings: {
            placeholder: "Queue journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : label?.label?.id
          ? label.label
          : await findAnnotationLabelByName(client, labelName);
      assert(createdLabel?.id, "Disposable label create did not return id.");
      cleanup.defer("delete disposable queue label", () =>
        client.delete(
          apiPath("/model-hub/annotations-labels/{id}/", {
            id: createdLabel.id,
          }),
        ),
      );

      const queueName = `api journey disposable queue ${runId}`;
      let ownsQueue = false;
      let queue;
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description:
            "Created by API journey and hard-deleted during cleanup.",
          instructions: "Disposable API journey queue.",
          assignment_strategy: "manual",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: false,
          auto_assign: false,
          label_ids: [createdLabel.id],
        });
        ownsQueue = true;
      } catch (error) {
        if (error.status !== 402) throw error;
        evidence.push({
          queue_create_blocked: "ENTITLEMENT_LIMIT",
          detail: String(error.body?.detail || error.message || "").slice(
            0,
            240,
          ),
        });
        queue = await resolveManagerQueue(client, evidence);
        await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: createdLabel.id, required: false },
        );
        cleanup.defer("remove disposable label from fallback queue", () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
              id: queue.id,
            }),
            { label_id: createdLabel.id },
          ),
        );
      }
      assert(queue?.id, "Disposable queue create did not return id.");
      if (ownsQueue) {
        cleanup.defer("hard-delete disposable queue", () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
              id: queue.id,
            }),
            { force: true, confirm_name: queueName },
          ),
        );
      }

      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));
      const beforeProgress = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queue.id,
        }),
      );

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            { source_type: "trace", source_id: sample.traceId },
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );

      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
            },
          },
        ),
      );
      const items = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      if (!ownsQueue && items.length) {
        cleanup.defer("delete disposable fallback queue items", async () => {
          for (const item of items) {
            await client.delete(
              queuePath(
                "/model-hub/annotation-queues/{queue_id}/items/{id}/",
                queue.id,
                { id: item.id },
              ),
            );
          }
        });
      }
      assert(
        added.added === 2 && items.length === 2,
        `Disposable queue expected two new items, added=${added.added} saw=${items.length}.`,
      );
      const itemSources = items.map((item) => ({
        id: item.id,
        source_type: item.source_type || item.sourceType,
        source_id: item.source_id || item.sourceId,
      }));
      const disposableIds = new Set(items.map((item) => String(item.id)));
      const sourceTypes = new Set(itemSources.map((item) => item.source_type));
      assert(
        sourceTypes.has("trace") && sourceTypes.has("observation_span"),
        `Disposable queue items did not include trace and span source types: ${JSON.stringify(
          itemSources,
        )}.`,
      );
      const traceEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        sample.traceId,
      );
      const spanEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "observation_span",
        sample.spanId,
      );
      assert(
        traceEntry?.item?.id &&
          disposableIds.has(String(traceEntry.item.id)) &&
          String(traceEntry.item.source_id) === String(sample.traceId),
        "for-source lookup did not return the disposable trace queue item with source_id.",
      );
      assert(
        spanEntry?.item?.id &&
          disposableIds.has(String(spanEntry.item.id)) &&
          String(spanEntry.item.source_id) === String(sample.spanId),
        "for-source lookup did not return the disposable span queue item with source_id.",
      );
      const sourceIdByItemId = new Map([
        [String(traceEntry.item.id), traceEntry.item.source_id],
        [String(spanEntry.item.id), spanEntry.item.source_id],
      ]);

      const firstNext = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/next-item/",
          queue.id,
        ),
      );
      const firstItem = firstNext.item;
      assert(
        firstItem?.id && disposableIds.has(String(firstItem.id)),
        "Next-item did not return one of the disposable items.",
      );

      const annotationText = `completed annotation ${runId}`;
      const labelNote = `label note ${runId}`;
      const itemNote = `whole item note ${runId}`;
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
          queue.id,
          { id: firstItem.id },
        ),
        {
          annotations: [
            {
              label_id: createdLabel.id,
              value: { text: annotationText },
              notes: labelNote,
            },
          ],
          item_notes: itemNote,
        },
      );
      const completed = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
          queue.id,
          { id: firstItem.id },
        ),
        { exclude: [...beforeIds, firstItem.id] },
      );
      assert(
        completed.completed_item_id === firstItem.id,
        "Complete response did not echo completed_item_id.",
      );
      const secondItem = completed.next_item;
      assert(
        secondItem?.id &&
          secondItem.id !== firstItem.id &&
          disposableIds.has(String(secondItem.id)),
        "Complete response did not return the next pending item.",
      );

      const skipped = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/skip/",
          queue.id,
          { id: secondItem.id },
        ),
        { exclude: [...beforeIds, firstItem.id, secondItem.id] },
      );
      assert(
        skipped.skipped_item_id === secondItem.id,
        "Skip response did not echo skipped_item_id.",
      );
      assert(
        !skipped.next_item || !disposableIds.has(String(skipped.next_item.id)),
        "Skip should not return another disposable item in a two-item journey set.",
      );

      const completedDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: firstItem.id },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        completedDetail.item?.status === "completed",
        `Completed item reloaded with status ${completedDetail.item?.status}.`,
      );
      assert(
        String(completedDetail.existing_notes || "").includes(itemNote),
        "Completed item notes did not reload through annotate detail.",
      );
      assert(
        asArray(completedDetail.annotations).some(
          (score) =>
            String(score.label_id) === String(createdLabel.id) &&
            sameJsonValue(score.value, { text: annotationText }) &&
            String(score.notes || "").includes(labelNote),
        ),
        "Completed item annotation did not reload through annotate detail.",
      );
      const completedScore = asArray(completedDetail.annotations).find(
        (score) =>
          String(score.label_id) === String(createdLabel.id) &&
          sameJsonValue(score.value, { text: annotationText }),
      );
      assert(
        completedScore?.id,
        "Completed item annotation did not expose a reloadable score id.",
      );

      const skippedDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: secondItem.id },
        ),
        { query: { include_completed: true } },
      );
      assert(
        skippedDetail.item?.status === "skipped",
        `Skipped item reloaded with status ${skippedDetail.item?.status}.`,
      );

      const navigationDb = await loadQueueNavigationDbAudit({
        queueId: queue.id,
        completedItemId: firstItem.id,
        skippedItemId: secondItem.id,
        labelId: createdLabel.id,
        userId,
      });
      assert(
        String(navigationDb.queue?.organization_id) ===
          String(organizationId) &&
          String(navigationDb.queue?.workspace_id) === String(workspaceId),
        `Queue navigation DB queue scope mismatch: ${JSON.stringify(
          navigationDb,
        )}.`,
      );
      assert(
        navigationDb.completed_item?.status === "completed" &&
          navigationDb.skipped_item?.status === "skipped" &&
          Number(navigationDb.completed_item?.source_fk_count) === 1 &&
          Number(navigationDb.skipped_item?.source_fk_count) === 1 &&
          String(navigationDb.completed_item?.organization_id) ===
            String(organizationId) &&
          String(navigationDb.skipped_item?.organization_id) ===
            String(organizationId) &&
          String(navigationDb.completed_item?.workspace_id) ===
            String(workspaceId) &&
          String(navigationDb.skipped_item?.workspace_id) ===
            String(workspaceId),
        `Queue navigation DB item state mismatch: ${JSON.stringify(
          navigationDb,
        )}.`,
      );
      assert(
        Number(navigationDb.completed_score_count) === 1 &&
          String(navigationDb.completed_score?.id) ===
            String(completedScore.id) &&
          sameJsonValue(navigationDb.completed_score?.value, {
            text: annotationText,
          }) &&
          String(navigationDb.completed_score?.notes || "").includes(
            labelNote,
          ) &&
          String(navigationDb.completed_item_note?.notes || "").includes(
            itemNote,
          ) &&
          Number(navigationDb.skipped_score_count) === 0,
        `Queue navigation DB score/note state mismatch: ${JSON.stringify(
          navigationDb,
        )}.`,
      );

      const progress = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/progress/", {
          id: queue.id,
        }),
      );
      if (ownsQueue) {
        assert(
          progress.total === 2,
          `Progress total expected 2, saw ${progress.total}.`,
        );
        assert(
          progress.completed === 1 && progress.skipped === 1,
          `Progress expected completed=1 skipped=1, saw ${JSON.stringify(progress)}.`,
        );
      } else {
        assert(
          Number(progress.total || 0) >= Number(beforeProgress.total || 0) + 2,
          `Fallback progress total did not increase by at least 2: before=${JSON.stringify(
            beforeProgress,
          )} after=${JSON.stringify(progress)}.`,
        );
        assert(
          Number(progress.completed || 0) >=
            Number(beforeProgress.completed || 0) + 1 &&
            Number(progress.skipped || 0) >=
              Number(beforeProgress.skipped || 0) + 1,
          `Fallback progress did not include completed/skipped deltas: before=${JSON.stringify(
            beforeProgress,
          )} after=${JSON.stringify(progress)}.`,
        );
      }

      const jsonExport = await client.get(
        withQuery(
          apiPath("/model-hub/annotation-queues/{id}/export/", {
            id: queue.id,
          }),
          { export_format: "json" },
        ),
      );
      assert(
        Array.isArray(jsonExport),
        "Queue JSON export did not return an array.",
      );
      const completedExport = jsonExport.find(
        (row) => String(row.item_id) === String(firstItem.id),
      );
      const skippedExport = jsonExport.find(
        (row) => String(row.item_id) === String(secondItem.id),
      );
      assert(
        completedExport?.status === "completed",
        "Queue JSON export did not include the completed disposable item.",
      );
      assert(
        skippedExport?.status === "skipped",
        "Queue JSON export did not include the skipped disposable item.",
      );
      assert(
        String(completedExport.source_id) ===
          String(sourceIdByItemId.get(String(firstItem.id))),
        "Queue JSON export did not preserve completed item source_id.",
      );
      assert(
        String(completedExport.item_notes || "").includes(itemNote),
        "Queue JSON export did not include the completed item note.",
      );
      assert(
        asArray(completedExport.annotations).some(
          (score) =>
            String(score.label_id) === String(createdLabel.id) &&
            sameJsonValue(score.value, { text: annotationText }) &&
            String(score.notes || "").includes(labelNote),
        ),
        "Queue JSON export did not include the submitted label annotation.",
      );

      const csvExport = await client.get(
        withQuery(
          apiPath("/model-hub/annotation-queues/{id}/export/", {
            id: queue.id,
          }),
          { export_format: "csv" },
        ),
      );
      const csvText = String(csvExport || "");
      assert(
        csvText.includes(String(firstItem.id)) &&
          csvText.includes(String(secondItem.id)) &&
          csvText.includes(labelNote),
        "Queue CSV export did not include disposable item ids and annotation notes.",
      );

      evidence.push({
        queue_id: queue.id,
        owns_queue: ownsQueue,
        label_id: createdLabel.id,
        trace_id: sample.traceId,
        span_id: sample.spanId,
        trace_queue_item_id: traceEntry.item.id,
        trace_for_source_id: traceEntry.item.source_id,
        span_queue_item_id: spanEntry.item.id,
        span_for_source_id: spanEntry.item.source_id,
        completed_item_id: firstItem.id,
        skipped_item_id: secondItem.id,
        completed_score_id: navigationDb.completed_score?.id,
        item_sources: itemSources,
        completed_item_source_fk_count:
          navigationDb.completed_item?.source_fk_count,
        skipped_item_source_fk_count:
          navigationDb.skipped_item?.source_fk_count,
        skipped_score_count: navigationDb.skipped_score_count,
        progress_delta_total:
          Number(progress.total || 0) - Number(beforeProgress.total || 0),
        progress_delta_completed:
          Number(progress.completed || 0) -
          Number(beforeProgress.completed || 0),
        progress_delta_skipped:
          Number(progress.skipped || 0) - Number(beforeProgress.skipped || 0),
        progress_total: progress.total,
        progress_completed: progress.completed,
        progress_skipped: progress.skipped,
        json_export_rows: jsonExport.length,
        csv_export_bytes: csvText.length,
      });
    },
  },
  {
    id: "AQ-API-011",
    title:
      "Queue item filters, created-at ordering, detail open, delete, and bulk remove",
    tags: ["annotation", "mutating", "filters", "navigation", "cleanup"],
    async run({ client, cleanup, evidence }) {
      requireMutations();
      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueue(client, evidence);
      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            { source_type: "trace", source_id: sample.traceId },
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );

      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const createdItems = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      const createdIds = new Set(createdItems.map((item) => String(item.id)));
      const removedIds = new Set();
      cleanup.defer("delete disposable filter journey items", async () => {
        for (const item of createdItems) {
          if (!removedIds.has(String(item.id))) {
            await deleteQueueItemIfPresent(client, queue.id, item.id);
          }
        }
      });

      assert(
        added.added === 2 && createdItems.length === 2,
        `Filter journey expected two new items, added=${added.added} saw=${createdItems.length}.`,
      );
      const traceItem = createdItems.find(
        (item) => item.source_type === "trace",
      );
      const spanItem = createdItems.find(
        (item) => item.source_type === "observation_span",
      );
      assert(traceItem?.id, "Trace filter journey item was not created.");
      assert(spanItem?.id, "Span filter journey item was not created.");

      const traceEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        sample.traceId,
      );
      const spanEntry = await findQueueEntryForSource(
        client,
        queue.id,
        "observation_span",
        sample.spanId,
      );
      assert(
        String(traceEntry?.item?.id) === String(traceItem.id) &&
          String(traceEntry.item.source_id) === String(sample.traceId),
        "for-source did not map the disposable trace item back to the requested source.",
      );
      assert(
        String(spanEntry?.item?.id) === String(spanItem.id) &&
          String(spanEntry.item.source_id) === String(sample.spanId),
        "for-source did not map the disposable span item back to the requested source.",
      );

      const pendingTraceRows = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: "pending",
              source_type: "trace",
              ordering: "-created_at",
            },
          },
        ),
      );
      assert(
        pendingTraceRows.some(
          (item) => String(item.id) === String(traceItem.id),
        ),
        "Pending trace filter did not include the disposable trace item.",
      );
      assert(
        pendingTraceRows.every(
          (item) => item.status === "pending" && item.source_type === "trace",
        ),
        "Pending trace filter returned rows outside status/source constraints.",
      );

      const pendingSpanRows = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending"],
              source_type: ["observation_span"],
            },
          },
        ),
      );
      assert(
        pendingSpanRows.some((item) => String(item.id) === String(spanItem.id)),
        "Pending span filter did not include the disposable span item.",
      );
      assert(
        pendingSpanRows.every(
          (item) =>
            item.status === "pending" &&
            item.source_type === "observation_span",
        ),
        "Pending span filter returned rows outside status/source constraints.",
      );

      const descendingRows = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending"],
              source_type: ["trace", "observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      ).filter((item) => createdIds.has(String(item.id)));
      const ascendingRows = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending"],
              source_type: ["trace", "observation_span"],
              ordering: "created_at",
            },
          },
        ),
      ).filter((item) => createdIds.has(String(item.id)));
      assert(
        descendingRows.length === 2 && ascendingRows.length === 2,
        "Created-at ordering queries did not return both disposable items.",
      );
      assert(
        sameJsonValue(
          descendingRows.map((item) => String(item.id)),
          ascendingRows.map((item) => String(item.id)).reverse(),
        ),
        "Ascending and descending created-at order were not stable opposites.",
      );

      const itemDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/",
          queue.id,
          { id: traceItem.id },
        ),
      );
      assert(
        itemDetail?.id === traceItem.id && itemDetail.source_type === "trace",
        "Queue item detail did not open the disposable trace item.",
      );

      const annotateDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: traceItem.id },
        ),
        { query: { include_completed: true } },
      );
      assert(
        annotateDetail?.item?.id === traceItem.id,
        "Annotate detail did not open the disposable trace item.",
      );

      await client.delete(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/",
          queue.id,
          { id: traceItem.id },
        ),
      );
      removedIds.add(String(traceItem.id));
      await expectItemMissing(client, queue.id, traceItem.id);

      const afterSingleDelete = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
            },
          },
        ),
      );
      assert(
        !afterSingleDelete.some(
          (item) => String(item.id) === String(traceItem.id),
        ) &&
          afterSingleDelete.some(
            (item) => String(item.id) === String(spanItem.id),
          ),
        "Single delete did not remove only the requested disposable item.",
      );

      const bulkRemoved = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/bulk-remove/",
          queue.id,
        ),
        { item_ids: [spanItem.id] },
      );
      assert(
        Number(bulkRemoved.removed || 0) === 1,
        `Bulk remove expected removed=1, saw ${JSON.stringify(bulkRemoved)}.`,
      );
      removedIds.add(String(spanItem.id));
      await expectItemMissing(client, queue.id, spanItem.id);

      const afterBulkRemove = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
            },
          },
        ),
      );
      assert(
        !afterBulkRemove.some((item) => createdIds.has(String(item.id))),
        "Removed disposable items still appeared in the queue item list.",
      );

      const traceEntryAfterDelete = await findQueueEntryForSource(
        client,
        queue.id,
        "trace",
        sample.traceId,
      );
      const spanEntryAfterDelete = await findQueueEntryForSource(
        client,
        queue.id,
        "observation_span",
        sample.spanId,
      );
      assert(
        !traceEntryAfterDelete && !spanEntryAfterDelete,
        "for-source lookup still returned deleted disposable queue items.",
      );

      evidence.push({
        queue_id: queue.id,
        trace_id: sample.traceId,
        span_id: sample.spanId,
        trace_queue_item_id: traceItem.id,
        trace_for_source_id: traceEntry.item.source_id,
        span_queue_item_id: spanItem.id,
        span_for_source_id: spanEntry.item.source_id,
        pending_trace_rows: pendingTraceRows.length,
        pending_span_rows: pendingSpanRows.length,
        ordering_desc_ids: descendingRows.map((item) => item.id),
        ordering_asc_ids: ascendingRows.map((item) => item.id),
        single_delete_missing: true,
        bulk_remove_removed: bulkRemoved.removed,
      });
    },
  },
  {
    id: "AQ-API-012",
    title: "Queue reservation conflict, release, transfer, and cleanup",
    tags: ["annotation", "mutating", "reservation", "data-roundtrip"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second active user to run reservation conflict coverage.",
        );
      }

      const primaryUserId = currentUserId(user);
      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      if (!primaryUserId || !altUserId) {
        skip("Could not resolve both current and alternate user ids.");
      }
      if (String(primaryUserId) === String(altUserId)) {
        skip("Alternate token resolved to the same user as the primary token.");
      }

      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveManagerQueue(client, evidence);
      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            { source_type: "trace", source_id: sample.traceId },
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );

      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["trace", "observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const createdItems = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      cleanup.defer("delete disposable reservation journey items", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queue.id, item.id);
        }
      });
      if (!createdItems.length) {
        skip(
          `Reservation journey could not create a disposable trace/span item; added=${JSON.stringify(
            added,
          )}.`,
        );
      }

      const reservedItem = createdItems[0];
      const annotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queue.id,
        { id: reservedItem.id },
      );
      const releasePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/release/",
        queue.id,
        { id: reservedItem.id },
      );

      const primaryReserve = await client.get(annotatePath, {
        query: { include_completed: true, reserve: true },
      });
      assert(
        primaryReserve?.item?.id === reservedItem.id,
        "Primary reserve did not return the disposable queue item.",
      );

      const primaryReentrantReserve = await client.get(annotatePath, {
        query: { include_completed: true, reserve: true },
      });
      assert(
        primaryReentrantReserve?.item?.id === reservedItem.id,
        "Primary re-entrant reserve did not return the same item.",
      );

      const altNextWhileReserved = await altClient.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/next-item/",
          queue.id,
        ),
      );
      assert(
        !altNextWhileReserved?.item ||
          String(altNextWhileReserved.item.id) !== String(reservedItem.id),
        "Next-item returned an item reserved by another user.",
      );

      const altConflictStatus = await expectHttpStatus(
        () =>
          altClient.get(annotatePath, {
            query: { include_completed: true, reserve: true },
          }),
        400,
      );

      const primaryRelease = await client.post(releasePath);
      assert(
        primaryRelease?.released === true,
        "Primary release response did not confirm release.",
      );

      const altReserve = await altClient.get(annotatePath, {
        query: { include_completed: true, reserve: true },
      });
      assert(
        altReserve?.item?.id === reservedItem.id,
        "Alternate user could not reserve after primary release.",
      );

      const primaryConflictStatus = await expectHttpStatus(
        () => client.post(releasePath),
        400,
      );

      const altRelease = await altClient.post(releasePath);
      assert(
        altRelease?.released === true,
        "Alternate release response did not confirm release.",
      );

      const finalReserve = await client.get(annotatePath, {
        query: { include_completed: true, reserve: true },
      });
      assert(
        finalReserve?.item?.id === reservedItem.id,
        "Primary user could not reserve again after alternate release.",
      );
      const finalRelease = await client.post(releasePath);
      assert(
        finalRelease?.released === true,
        "Final release response did not confirm release.",
      );

      evidence.push({
        queue_id: queue.id,
        item_id: reservedItem.id,
        source_type: reservedItem.source_type,
        source_id:
          reservedItem.source_id ||
          reservedItem.sourceId ||
          (reservedItem.source_type === "trace"
            ? sample.traceId
            : sample.spanId),
        primary_user_id: primaryUserId,
        alt_user_id: altUserId,
        created_item_count: createdItems.length,
        alt_conflict_status: altConflictStatus,
        primary_conflict_status: primaryConflictStatus,
        alt_next_excluded_reserved_item: true,
        final_release: true,
      });
    },
  },
  {
    id: "AQ-API-013",
    title:
      "Queue export-to-dataset mapped columns and dataset table round-trip",
    tags: ["annotation", "mutating", "export", "dataset", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence, user }) {
      requireMutations();
      const userId = assertCurrentUserResolved(user);
      const sample = await resolveTraceAndSpanSample(client);
      const labelName = `api journey export label ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Disposable label for queue export-to-dataset journey.",
          settings: {
            placeholder: "Queue export",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : label?.label?.id
          ? label.label
          : await findAnnotationLabelByName(client, labelName);
      assert(createdLabel?.id, "Export label create did not return id.");
      cleanup.defer("delete export journey label", () =>
        client.delete(
          apiPath("/model-hub/annotations-labels/{id}/", {
            id: createdLabel.id,
          }),
        ),
      );

      const queueName = `api journey export queue ${runId}`;
      let ownsQueue = false;
      let queue;
      try {
        queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
          name: queueName,
          description:
            "Created by API journey and hard-deleted during cleanup.",
          instructions: "Disposable API journey export queue.",
          assignment_strategy: "manual",
          annotations_required: 1,
          reservation_timeout_minutes: 30,
          requires_review: false,
          auto_assign: false,
          label_ids: [createdLabel.id],
        });
        ownsQueue = true;
      } catch (error) {
        if (error.status !== 402) throw error;
        evidence.push({
          queue_create_blocked: "ENTITLEMENT_LIMIT",
          detail: String(error.body?.detail || error.message || "").slice(
            0,
            240,
          ),
        });
        queue = await resolveManagerQueue(client, evidence);
        await client.post(
          apiPath("/model-hub/annotation-queues/{id}/add-label/", {
            id: queue.id,
          }),
          { label_id: createdLabel.id, required: false },
        );
        cleanup.defer("remove export label from fallback queue", () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
              id: queue.id,
            }),
            { label_id: createdLabel.id },
          ),
        );
      }
      assert(queue?.id, "Export queue create/resolve did not return id.");
      if (ownsQueue) {
        cleanup.defer("hard-delete export journey queue", () =>
          client.post(
            apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
              id: queue.id,
            }),
            { force: true, confirm_name: queueName },
          ),
        );
      }

      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queue.id,
        ),
        {
          items: [
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );

      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queue.id),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const createdItems = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      cleanup.defer("delete export journey queue items", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queue.id, item.id);
        }
      });
      assert(
        added.added === 1 && createdItems.length === 1,
        `Export journey expected one new item, added=${added.added} saw=${createdItems.length}.`,
      );
      const item = createdItems[0];

      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queue.id,
        ),
        {
          item_ids: [item.id],
          user_ids: [userId],
          action: "set",
        },
      );
      cleanup.defer("unassign export journey queue item", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queue.id,
          ),
          {
            item_ids: [item.id],
            user_ids: [],
            action: "set",
          },
        ),
      );

      const annotationText = `export annotation ${runId}`;
      const labelNote = `export label note ${runId}`;
      const itemNote = `export item note ${runId}`;
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
          queue.id,
          { id: item.id },
        ),
        {
          annotations: [
            {
              label_id: createdLabel.id,
              value: { text: annotationText },
              notes: labelNote,
            },
          ],
          item_notes: itemNote,
        },
      );
      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
          queue.id,
          { id: item.id },
        ),
        { exclude: [...beforeIds, item.id] },
      );
      const completedDetail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        { query: { include_completed: true, include_all_annotations: true } },
      );
      assert(
        ["completed", "in_progress"].includes(completedDetail.item?.status),
        `Export item reloaded with unexpected status ${completedDetail.item?.status}.`,
      );
      assert(
        asArray(completedDetail.annotations).some(
          (score) =>
            String(score.label_id) === String(createdLabel.id) &&
            sameJsonValue(score.value, { text: annotationText }) &&
            String(score.notes || "").includes(labelNote),
        ),
        "Export item annotation did not reload before dataset export.",
      );

      const fieldsPayload = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/export-fields/", {
          id: queue.id,
        }),
      );
      const fields = asArray(fieldsPayload.fields);
      const labelSlotValueField = `label:${createdLabel.id}:slot:1:value`;
      const labelSlotNotesField = `label:${createdLabel.id}:slot:1:notes`;
      assert(
        fields.some((field) => field.id === labelSlotValueField) &&
          fields.some((field) => field.id === labelSlotNotesField),
        "Export fields did not include disposable label slot fields.",
      );

      const datasetName = `api journey queue export dataset ${runId}`;
      const exportStatusFilter = completedDetail.item.status || "completed";
      const exported = await client.post(
        apiPath("/model-hub/annotation-queues/{id}/export-to-dataset/", {
          id: queue.id,
        }),
        {
          dataset_name: datasetName,
          status_filter: exportStatusFilter,
          column_mapping: [
            { field: "source_id", column: "source_identifier", enabled: true },
            { field: "source_type", column: "source_type", enabled: true },
            { field: "status", column: "queue_status", enabled: true },
            { field: "item_notes", column: "item_notes", enabled: true },
            {
              field: "annotation_metrics",
              column: "annotation_metrics",
              enabled: true,
            },
            {
              field: labelSlotValueField,
              column: "export_label_value",
              enabled: true,
            },
            {
              field: labelSlotNotesField,
              column: "export_label_notes",
              enabled: true,
            },
          ],
        },
      );
      const datasetId = exported?.dataset_id;
      assert(datasetId, "Export-to-dataset did not return dataset_id.");
      cleanup.defer("delete export journey dataset", () =>
        deleteDatasetIfPresent(client, datasetId),
      );
      assert(
        Number(exported.rows_created || 0) >= 1,
        `Export-to-dataset did not create any rows: ${JSON.stringify(exported)}.`,
      );
      assert(
        asArray(exported.columns).includes("export_label_value"),
        "Export-to-dataset response did not include the mapped label value column.",
      );

      const table = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
          dataset_id: datasetId,
        }),
        { query: { page_size: 100, current_page_index: 0 } },
      );
      const tableRows = asArray(table);
      const columns = Array.isArray(table.column_config)
        ? table.column_config
        : [];
      const valueColumn = columns.find(
        (column) => column.name === "export_label_value",
      );
      const notesColumn = columns.find(
        (column) => column.name === "export_label_notes",
      );
      const sourceColumn = columns.find(
        (column) => column.name === "source_identifier",
      );
      assert(
        valueColumn?.id && notesColumn?.id && sourceColumn?.id,
        "Dataset table did not expose mapped export columns by name.",
      );
      let exportedRow = tableRows.find((row) =>
        String(row[valueColumn.id]?.cell_value || "").includes(annotationText),
      );
      const totalPages = Number(table.metadata?.total_pages || 1);
      for (let page = 1; !exportedRow && page < totalPages; page += 1) {
        const nextPage = await client.get(
          apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
            dataset_id: datasetId,
          }),
          { query: { page_size: 100, current_page_index: page } },
        );
        exportedRow = asArray(nextPage).find((row) =>
          String(row[valueColumn.id]?.cell_value || "").includes(
            annotationText,
          ),
        );
      }
      assert(
        exportedRow,
        "Dataset table did not contain the exported disposable annotation value.",
      );
      assert(
        String(exportedRow[notesColumn.id]?.cell_value || "").includes(
          labelNote,
        ),
        "Dataset table did not contain the exported disposable annotation note.",
      );
      assert(
        String(exportedRow[sourceColumn.id]?.cell_value || "") ===
          String(sample.spanId),
        "Dataset table source_identifier cell did not match the exported span id.",
      );

      evidence.push({
        queue_id: queue.id,
        owns_queue: ownsQueue,
        label_id: createdLabel.id,
        assigned_to: userId,
        item_id: item.id,
        source_type: "observation_span",
        source_id: sample.spanId,
        item_status_exported: exportStatusFilter,
        dataset_id: datasetId,
        dataset_name: exported.dataset_name,
        rows_created: exported.rows_created,
        mapped_columns: exported.columns,
        table_total_rows: table.metadata?.total_rows,
        exported_row_id: exportedRow.row_id,
        value_column_id: valueColumn.id,
        notes_column_id: notesColumn.id,
      });
    },
  },
  {
    id: "AQ-API-014",
    title:
      "Review request changes, annotator resubmission, approval, and DB-audit ids",
    tags: ["annotation", "mutating", "review", "data-roundtrip"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const reviewerId = assertCurrentUserResolved(user);
      const altToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      if (!altToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN for a second active annotator to run review lifecycle coverage.",
        );
      }

      const altClient = createApiClient({
        apiBase,
        accessToken: altToken,
        organizationId,
        workspaceId,
      });
      const altUser = await altClient.get(apiPath("/accounts/user-info/"));
      const altUserId = currentUserId(altUser);
      assert(altUserId, "Alternate user id could not be resolved.");
      if (String(altUserId) === String(reviewerId)) {
        skip("Alternate token resolved to the reviewer user.");
      }

      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveReviewQueueWithAnnotator(
        client,
        altUserId,
        evidence,
      );
      const queueId = queue.id;

      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));

      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queueId,
        ),
        {
          items: [
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );

      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const createdItems = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      cleanup.defer("delete review lifecycle queue item", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queueId, item.id);
        }
      });
      assert(
        added.added === 1 && createdItems.length === 1,
        `Review journey expected one new item, added=${added.added} saw=${createdItems.length} duplicates=${added.duplicates}.`,
      );
      const item = createdItems[0];

      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queueId,
        ),
        {
          item_ids: [item.id],
          user_ids: [altUserId],
          action: "set",
        },
      );
      cleanup.defer("unassign review lifecycle queue item", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queueId,
          ),
          {
            item_ids: [item.id],
            user_ids: [],
            action: "set",
          },
        ),
      );

      const annotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queueId,
        { id: item.id },
      );
      const submitPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
        queueId,
        { id: item.id },
      );
      const completePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
        queueId,
        { id: item.id },
      );
      const reviewPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
        queueId,
        { id: item.id },
      );

      const initialDetail = await altClient.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        initialDetail?.item?.id === item.id,
        "Alternate annotator could not open the disposable review item.",
      );
      const labels = asArray(initialDetail.labels).filter((label) =>
        labelId(label),
      );
      const requiredLabels = labels.filter((label) => label.required);
      const labelsToSubmit = requiredLabels.length ? requiredLabels : labels;
      assert(
        labelsToSubmit.length > 0,
        "Review queue item did not expose any labels to submit.",
      );

      const initialValues = new Map();
      const firstAnnotations = labelsToSubmit.map((label, index) => {
        const value = reviewAnnotationValue(label, runId, `initial-${index}`);
        initialValues.set(String(labelId(label)), value);
        return {
          label_id: labelId(label),
          value,
          notes: label.allow_notes ? `review setup note ${runId}` : "",
        };
      });
      const itemNote = `review lifecycle item note ${runId}`;
      const submitted = await altClient.post(submitPath, {
        annotations: firstAnnotations,
        item_notes: itemNote,
      });
      assert(
        Number(submitted.submitted || 0) === firstAnnotations.length,
        `Initial review annotations submitted=${submitted.submitted}, expected ${firstAnnotations.length}.`,
      );

      const completed = await altClient.post(completePath, {
        exclude: [...beforeIds, item.id],
      });
      assert(
        completed.completed_item_id === item.id,
        "Initial complete did not echo the disposable item id.",
      );

      const pendingReviewDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        pendingReviewDetail.item?.status === "in_progress" &&
          pendingReviewDetail.item?.review_status === "pending_review",
        `Completing a requires-review item did not move it to pending_review: ${JSON.stringify(
          pendingReviewDetail.item,
        )}.`,
      );

      const targetLabel =
        labelsToSubmit.find((label) =>
          String(label.type || "")
            .toLowerCase()
            .includes("text"),
        ) || labelsToSubmit[0];
      const targetLabelId = labelId(targetLabel);
      const overallFeedback = `api journey review overall ${runId}`;
      const labelFeedback = `api journey review label ${runId}`;
      const requestedChanges = await client.post(reviewPath, {
        action: "request_changes",
        notes: overallFeedback,
        label_comments: [
          {
            label_id: targetLabelId,
            target_annotator_id: altUserId,
            comment: labelFeedback,
          },
        ],
      });
      assert(
        requestedChanges.action === "request_changes",
        "Review response did not echo request_changes action.",
      );
      const requestedComments = asArray(requestedChanges.review_comments);
      assert(
        requestedComments.some(
          (comment) =>
            comment.comment === overallFeedback &&
            comment.action === "request_changes" &&
            comment.thread_scope === "item" &&
            comment.blocking === false,
        ),
        "Request-changes response did not include non-blocking item-level context feedback.",
      );
      const labelChangeComment = requestedComments.find(
        (comment) =>
          comment.comment === labelFeedback &&
          comment.action === "request_changes" &&
          String(comment.label_id) === String(targetLabelId) &&
          String(comment.target_annotator_id) === String(altUserId),
      );
      assert(
        labelChangeComment?.thread_id,
        "Request-changes response did not include score-scoped feedback for the target annotator.",
      );
      assert(
        labelChangeComment.thread_scope === "score" &&
          labelChangeComment.blocking === true &&
          labelChangeComment.thread_status === "open",
        `Label request-change comment had wrong thread shape: ${JSON.stringify(
          labelChangeComment,
        )}.`,
      );
      const blockingThreadId = labelChangeComment.thread_id;
      const itemThread = asArray(requestedChanges.review_threads).find(
        (thread) => thread.scope === "item" && thread.blocking === false,
      );
      assert(
        itemThread?.id,
        "Request-changes response did not include the item-level review thread.",
      );
      const blockedApproveStatus = await expectHttpStatus(
        () => client.post(reviewPath, { action: "approve" }),
        400,
      );

      const altFeedbackDetail = await altClient.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        asArray(altFeedbackDetail.review_comments).some(
          (comment) =>
            comment.comment === labelFeedback &&
            String(comment.target_annotator_id) === String(altUserId),
        ),
        "Target annotator did not see their score-scoped request-change feedback.",
      );

      const revisedValue = revisedReviewAnnotationValue(
        targetLabel,
        initialValues.get(String(targetLabelId)),
        runId,
      );
      const revisionNote = `review lifecycle revision note ${runId}`;
      await altClient.post(submitPath, {
        annotations: [
          {
            label_id: targetLabelId,
            value: revisedValue,
            notes: targetLabel.allow_notes ? revisionNote : "",
          },
        ],
      });
      await altClient.post(completePath, { exclude: [...beforeIds, item.id] });

      const addressedDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      const addressedThread = asArray(addressedDetail.review_threads).find(
        (thread) => String(thread.id) === String(blockingThreadId),
      );
      assert(
        addressedThread?.status === "addressed",
        `Resubmission did not mark the blocking feedback addressed: ${JSON.stringify(
          addressedThread,
        )}.`,
      );
      assert(
        asArray(addressedDetail.review_comments).some(
          (comment) =>
            comment.action === "request_changes" &&
            comment.thread_status === "addressed" &&
            String(comment.thread_id) === String(blockingThreadId),
        ),
        "Resubmission did not reload the targeted feedback with addressed thread status.",
      );
      assert(
        addressedDetail.item?.workflow_status === "resubmitted",
        `Reviewer detail did not expose resubmitted workflow status: ${addressedDetail.item?.workflow_status}.`,
      );
      assert(
        asArray(addressedDetail.annotations).some(
          (score) =>
            String(score.label_id) === String(targetLabelId) &&
            String(score.annotator) === String(altUserId) &&
            sameJsonValue(score.value, revisedValue),
        ),
        "Revised target annotation did not reload for reviewer before approval.",
      );

      const approveNote = `api journey review approved ${runId}`;
      const approved = await client.post(reviewPath, {
        action: "approve",
        notes: approveNote,
      });
      assert(
        approved.action === "approve",
        "Approve response action mismatch.",
      );
      const approvedThread = asArray(approved.review_threads).find(
        (thread) => String(thread.id) === String(blockingThreadId),
      );
      assert(
        approvedThread?.status === "resolved",
        "Approve did not resolve the addressed blocking feedback thread.",
      );
      assert(
        asArray(approved.review_comments).some(
          (comment) =>
            comment.action === "approve" && comment.comment === approveNote,
        ),
        "Approve response did not include the approval comment.",
      );

      const finalDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        finalDetail.item?.status === "completed" &&
          finalDetail.item?.review_status === "approved",
        `Final review item status mismatch: ${JSON.stringify(finalDetail.item)}.`,
      );
      const finalTargetScore = asArray(finalDetail.annotations).find(
        (score) =>
          String(score.label_id) === String(targetLabelId) &&
          String(score.annotator) === String(altUserId),
      );
      assert(
        finalTargetScore?.id &&
          sameJsonValue(finalTargetScore.value, revisedValue),
        "Final approved detail did not keep the revised target annotation.",
      );

      evidence.push({
        queue_id: queueId,
        queue_name: queue.name,
        item_id: item.id,
        source_type: "observation_span",
        source_id: sample.spanId,
        reviewer_id: reviewerId,
        annotator_id: altUserId,
        label_count_submitted: firstAnnotations.length,
        target_label_id: targetLabelId,
        target_score_id: finalTargetScore.id,
        item_thread_id: itemThread.id,
        blocking_thread_id: blockingThreadId,
        item_thread_status_after_approve:
          asArray(approved.review_threads).find(
            (thread) => String(thread.id) === String(itemThread.id),
          )?.status || null,
        blocking_thread_status_after_address: addressedThread.status,
        blocking_thread_status_after_approve: approvedThread.status,
        blocked_approve_status: blockedApproveStatus,
        final_item_status: finalDetail.item.status,
        final_review_status: finalDetail.item.review_status,
      });
    },
  },
  {
    id: "AQ-API-015",
    title:
      "Targeted review feedback excludes non-target annotator and preserves scores",
    tags: ["annotation", "mutating", "review", "multi-user", "data-roundtrip"],
    async run({
      apiBase,
      client,
      cleanup,
      evidence,
      organizationId,
      runId,
      user,
      workspaceId,
    }) {
      requireMutations();
      const reviewerId = assertCurrentUserResolved(user);
      const targetToken = process.env.API_JOURNEY_ALT_ACCESS_TOKEN || "";
      const nonTargetToken =
        process.env.API_JOURNEY_NONTARGET_ACCESS_TOKEN ||
        process.env.API_JOURNEY_THIRD_ACCESS_TOKEN ||
        "";
      if (!targetToken || !nonTargetToken) {
        skip(
          "Set API_JOURNEY_ALT_ACCESS_TOKEN and API_JOURNEY_NONTARGET_ACCESS_TOKEN for two-annotator targeted review coverage.",
        );
      }

      const targetClient = createApiClient({
        apiBase,
        accessToken: targetToken,
        organizationId,
        workspaceId,
      });
      const nonTargetClient = createApiClient({
        apiBase,
        accessToken: nonTargetToken,
        organizationId,
        workspaceId,
      });
      const targetUser = await targetClient.get(
        apiPath("/accounts/user-info/"),
      );
      const nonTargetUser = await nonTargetClient.get(
        apiPath("/accounts/user-info/"),
      );
      const targetUserId = currentUserId(targetUser);
      const nonTargetUserId = currentUserId(nonTargetUser);
      assert(targetUserId, "Target annotator user id could not be resolved.");
      assert(
        nonTargetUserId,
        "Non-target annotator user id could not be resolved.",
      );
      assert(
        ![targetUserId, nonTargetUserId].some(
          (id) => String(id) === String(reviewerId),
        ) && String(targetUserId) !== String(nonTargetUserId),
        "Reviewer, target annotator, and non-target annotator must be distinct users.",
      );

      const sample = await resolveTraceAndSpanSample(client);
      const queue = await resolveReviewQueueWithAnnotator(
        client,
        targetUserId,
        evidence,
      );
      const queueId = queue.id;
      await ensureTemporaryAnnotatorMember(
        client,
        queue,
        nonTargetUserId,
        cleanup,
        evidence,
      );
      const nonTargetQueueDetail = await nonTargetClient.get(
        apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
      );
      const nonTargetRoles = asArray(nonTargetQueueDetail.viewer_roles);
      assert(
        nonTargetRoles.includes("annotator") &&
          !nonTargetRoles.some((role) =>
            ["manager", "reviewer"].includes(role),
          ),
        `Non-target user must be annotator-only for visibility checks, saw roles ${nonTargetRoles.join(
          ",",
        )}.`,
      );

      const beforeItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const beforeIds = new Set(beforeItems.map((item) => String(item.id)));
      const added = await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/add-items/",
          queueId,
        ),
        {
          items: [
            { source_type: "observation_span", source_id: sample.spanId },
          ],
        },
      );
      const afterAddItems = asArray(
        await client.get(
          queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
              source_type: ["observation_span"],
              ordering: "-created_at",
            },
          },
        ),
      );
      const createdItems = afterAddItems.filter(
        (item) => !beforeIds.has(String(item.id)),
      );
      cleanup.defer("delete two-annotator review queue item", async () => {
        for (const item of createdItems) {
          await deleteQueueItemIfPresent(client, queueId, item.id);
        }
      });
      assert(
        added.added === 1 && createdItems.length === 1,
        `Two-annotator review journey expected one new item, added=${added.added} saw=${createdItems.length}.`,
      );
      const item = createdItems[0];

      await client.post(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/assign/",
          queueId,
        ),
        {
          item_ids: [item.id],
          user_ids: [targetUserId, nonTargetUserId],
          action: "set",
        },
      );
      cleanup.defer("unassign two-annotator review queue item", () =>
        client.post(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/assign/",
            queueId,
          ),
          {
            item_ids: [item.id],
            user_ids: [],
            action: "set",
          },
        ),
      );

      const annotatePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
        queueId,
        { id: item.id },
      );
      const submitPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/",
        queueId,
        { id: item.id },
      );
      const completePath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/complete/",
        queueId,
        { id: item.id },
      );
      const reviewPath = queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
        queueId,
        { id: item.id },
      );

      const initialDetail = await targetClient.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      const labels = asArray(initialDetail.labels).filter((label) =>
        labelId(label),
      );
      const requiredLabels = labels.filter((label) => label.required);
      const labelsToSubmit = requiredLabels.length ? requiredLabels : labels;
      assert(
        labelsToSubmit.length > 0,
        "Two-annotator review item did not expose labels.",
      );
      const targetLabel =
        labelsToSubmit.find((label) =>
          String(label.type || "")
            .toLowerCase()
            .includes("text"),
        ) || labelsToSubmit[0];
      const targetLabelId = labelId(targetLabel);

      const targetInitialValues = new Map();
      const targetAnnotations = labelsToSubmit.map((label, index) => {
        const value = reviewAnnotationValue(label, runId, `target-${index}`);
        targetInitialValues.set(String(labelId(label)), value);
        return {
          label_id: labelId(label),
          value,
          notes: label.allow_notes ? `target label note ${runId}` : "",
        };
      });
      const targetSubmitted = await targetClient.post(submitPath, {
        annotations: targetAnnotations,
      });
      assert(
        Number(targetSubmitted.submitted || 0) === targetAnnotations.length,
        `Target annotator submitted=${targetSubmitted.submitted}, expected ${targetAnnotations.length}.`,
      );
      await targetClient.post(completePath, {
        exclude: [...beforeIds, item.id],
      });

      const nonTargetInitialValues = new Map();
      const nonTargetAnnotations = labelsToSubmit.map((label, index) => {
        const value = reviewAnnotationValue(label, runId, `nontarget-${index}`);
        nonTargetInitialValues.set(String(labelId(label)), value);
        return {
          label_id: labelId(label),
          value,
          notes: label.allow_notes ? `non-target label note ${runId}` : "",
        };
      });
      const nonTargetSubmitted = await nonTargetClient.post(submitPath, {
        annotations: nonTargetAnnotations,
      });
      assert(
        Number(nonTargetSubmitted.submitted || 0) ===
          nonTargetAnnotations.length,
        `Non-target annotator submitted=${nonTargetSubmitted.submitted}, expected ${nonTargetAnnotations.length}.`,
      );
      await nonTargetClient.post(completePath, {
        exclude: [...beforeIds, item.id],
      });

      const pendingReviewDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        pendingReviewDetail.item?.review_status === "pending_review",
        `Two-annotator item did not stay pending_review after submissions: ${JSON.stringify(
          pendingReviewDetail.item,
        )}.`,
      );
      assert(
        asArray(pendingReviewDetail.annotations).some(
          (score) =>
            String(score.label_id) === String(targetLabelId) &&
            String(score.annotator) === String(targetUserId),
        ) &&
          asArray(pendingReviewDetail.annotations).some(
            (score) =>
              String(score.label_id) === String(targetLabelId) &&
              String(score.annotator) === String(nonTargetUserId),
          ),
        "Reviewer detail did not include both annotators' target-label scores before review.",
      );

      const labelFeedback = `target-only review feedback ${runId}`;
      const requestedChanges = await client.post(reviewPath, {
        action: "request_changes",
        label_comments: [
          {
            label_id: targetLabelId,
            target_annotator_id: targetUserId,
            comment: labelFeedback,
          },
        ],
      });
      const targetThread = asArray(requestedChanges.review_threads).find(
        (thread) =>
          thread.scope === "score" &&
          thread.blocking === true &&
          String(thread.target_annotator_id) === String(targetUserId),
      );
      assert(
        targetThread?.id,
        "Targeted request-changes response did not expose the score-scoped blocking thread.",
      );

      const targetFeedbackDetail = await targetClient.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        asArray(targetFeedbackDetail.review_comments).some(
          (comment) =>
            comment.comment === labelFeedback &&
            String(comment.target_annotator_id) === String(targetUserId),
        ),
        "Target annotator did not see targeted feedback.",
      );

      const nonTargetDeniedStatus = await expectHttpStatus(
        () =>
          nonTargetClient.get(annotatePath, {
            query: { include_completed: true, include_all_annotations: true },
          }),
        403,
      );
      const nonTargetNext = await nonTargetClient.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/next-item/",
          queueId,
        ),
      );
      assert(
        !nonTargetNext?.item ||
          String(nonTargetNext.item.id) !== String(item.id),
        "Non-target next-item routed an item sent back to another annotator.",
      );

      const revisedValue = revisedReviewAnnotationValue(
        targetLabel,
        targetInitialValues.get(String(targetLabelId)),
        runId,
      );
      await targetClient.post(submitPath, {
        annotations: [
          {
            label_id: targetLabelId,
            value: revisedValue,
            notes: targetLabel.allow_notes
              ? `target revision note ${runId}`
              : "",
          },
        ],
      });
      await targetClient.post(completePath, {
        exclude: [...beforeIds, item.id],
      });

      const addressedDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      const addressedThread = asArray(addressedDetail.review_threads).find(
        (thread) => String(thread.id) === String(targetThread.id),
      );
      assert(
        addressedThread?.status === "addressed",
        "Targeted feedback thread was not addressed after target annotator revision.",
      );
      const targetScoreAfterRevision = asArray(
        addressedDetail.annotations,
      ).find(
        (score) =>
          String(score.label_id) === String(targetLabelId) &&
          String(score.annotator) === String(targetUserId),
      );
      const nonTargetScoreAfterRevision = asArray(
        addressedDetail.annotations,
      ).find(
        (score) =>
          String(score.label_id) === String(targetLabelId) &&
          String(score.annotator) === String(nonTargetUserId),
      );
      assert(
        targetScoreAfterRevision?.id &&
          sameJsonValue(targetScoreAfterRevision.value, revisedValue),
        "Target annotator score was not updated to the revised value.",
      );
      assert(
        nonTargetScoreAfterRevision?.id &&
          sameJsonValue(
            nonTargetScoreAfterRevision.value,
            nonTargetInitialValues.get(String(targetLabelId)),
          ),
        "Non-target annotator score changed during target annotator revision.",
      );

      const approved = await client.post(reviewPath, {
        action: "approve",
        notes: `target-only review approved ${runId}`,
      });
      const approvedThread = asArray(approved.review_threads).find(
        (thread) => String(thread.id) === String(targetThread.id),
      );
      assert(
        approvedThread?.status === "resolved",
        "Approve did not resolve targeted feedback thread.",
      );
      const finalDetail = await client.get(annotatePath, {
        query: { include_completed: true, include_all_annotations: true },
      });
      assert(
        finalDetail.item?.status === "completed" &&
          finalDetail.item?.review_status === "approved",
        `Two-annotator final item status mismatch: ${JSON.stringify(
          finalDetail.item,
        )}.`,
      );

      evidence.push({
        queue_id: queueId,
        queue_name: queue.name,
        item_id: item.id,
        source_type: "observation_span",
        source_id: sample.spanId,
        reviewer_id: reviewerId,
        target_annotator_id: targetUserId,
        non_target_annotator_id: nonTargetUserId,
        target_label_id: targetLabelId,
        target_thread_id: targetThread.id,
        target_score_id: targetScoreAfterRevision.id,
        non_target_score_id: nonTargetScoreAfterRevision.id,
        labels_submitted_per_annotator: labelsToSubmit.length,
        target_thread_status_after_address: addressedThread.status,
        target_thread_status_after_approve: approvedThread.status,
        non_target_denied_target_feedback_status: nonTargetDeniedStatus,
        non_target_next_excluded_target_item: true,
        final_item_status: finalDetail.item.status,
        final_review_status: finalDetail.item.review_status,
      });
    },
  },
];

async function findAnnotationLabelByName(client, name) {
  const rows = asArray(
    await client.get(apiPath("/model-hub/annotations-labels/"), {
      query: { search: name },
    }),
  );
  return rows.find((label) => label.name === name);
}

async function resolveReviewQueueWithAnnotator(client, annotatorId, evidence) {
  const selected = await tryResolveReviewQueueWithAnnotator(
    client,
    annotatorId,
    evidence,
  );
  if (selected) return selected;
  skip(
    "No active requires-review queue is available where the primary user can review and alternate user can annotate.",
  );
}

async function tryResolveReviewQueueWithAnnotator(
  client,
  annotatorId,
  evidence,
) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100, include_counts: true },
    }),
  );
  const candidates = [];
  for (const candidate of queues) {
    if (
      !candidate?.id ||
      candidate.status !== "active" ||
      !candidate.requires_review
    ) {
      continue;
    }
    const detail = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: candidate.id }),
    );
    const roles = asArray(detail.viewer_roles);
    if (!roles.some((role) => ["manager", "reviewer"].includes(role))) {
      continue;
    }
    const annotators = asArray(detail.annotators);
    if (
      !annotators.some(
        (annotator) => String(annotator.user_id) === String(annotatorId),
      )
    ) {
      continue;
    }
    const labels = asArray(detail.labels || detail.queue_labels);
    if (!labels.some((label) => labelId(label))) continue;
    candidates.push({
      detail,
      itemCount: Number(candidate.item_count ?? detail.item_count ?? 0),
      labelCount: labels.length,
    });
  }
  candidates.sort(
    (left, right) =>
      left.itemCount - right.itemCount || left.labelCount - right.labelCount,
  );
  const selected = candidates[0];
  if (selected) {
    evidence.push({
      review_queue_id: selected.detail.id,
      review_queue_name: selected.detail.name,
      review_queue_item_count: selected.itemCount,
      review_queue_label_count: selected.labelCount,
      review_queue_requires_review: Boolean(selected.detail.requires_review),
    });
    return selected.detail;
  }
  return null;
}

async function resolveOrCreateReviewQueueWithAnnotator(
  client,
  annotatorId,
  reviewerId,
  cleanup,
  evidence,
  runId,
) {
  const existing = await tryResolveReviewQueueWithAnnotator(
    client,
    annotatorId,
    evidence,
  );
  if (existing) return existing;

  const namePrefix = `api journey bulk review ${runId}`;
  const queueName = `${namePrefix} queue`;
  const labelName = `${namePrefix} text label`;
  cleanup.defer("hard-delete bulk-review queue DB fixtures", () =>
    deleteQueueCreateFixturesDb(namePrefix),
  );

  const labelResponse = await client.post(
    apiPath("/model-hub/annotations-labels/"),
    {
      name: labelName,
      type: "text",
      description: "Disposable label for bulk review API coverage.",
      settings: {
        placeholder: "Bulk review coverage",
        min_length: 0,
        max_length: 500,
      },
      allow_notes: true,
    },
  );
  const label = labelResponse?.id
    ? labelResponse
    : await findAnnotationLabelByName(client, labelName);
  assert(label?.id, "Could not resolve created bulk-review label.");
  cleanup.defer("delete bulk-review label", () =>
    deleteAnnotationLabelIfPresent(client, label.id),
  );

  let queue;
  let createMode = "api";
  try {
    queue = await client.post(apiPath("/model-hub/annotation-queues/"), {
      name: queueName,
      description: "Disposable queue for bulk review and discussion coverage.",
      instructions: "Verify bulk review and discussion comment edit/delete.",
      annotations_required: 1,
      reservation_timeout_minutes: 30,
      requires_review: true,
      auto_assign: false,
      label_ids: [label.id],
      annotator_ids: [reviewerId, annotatorId],
      annotator_roles: {
        [String(reviewerId)]: ["manager", "reviewer", "annotator"],
        [String(annotatorId)]: ["annotator"],
      },
    });
  } catch (error) {
    if (![402, 403].includes(error.status)) throw error;
    createMode = `db_seeded_after_review_create_${error.status}`;
    evidence.push({
      bulk_review_queue_create_entitlement_status: error.status,
      bulk_review_queue_create_entitlement_body: error.body,
    });
    queue = await insertBulkReviewQueueFixtureDb({
      queueName,
      labelId: label.id,
      reviewerId,
      annotatorId,
    });
  }
  assert(
    queue?.id,
    "Bulk-review queue create/seed did not produce a queue id.",
  );
  cleanup.defer("hard-delete bulk-review queue", () =>
    hardDeleteQueueIfPresent(client, queue.id, queueName),
  );

  await restoreQueueStatusIfNeeded(client, queue.id, "active");
  const detail = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
  );
  evidence.push({
    review_queue_id: detail.id,
    review_queue_name: detail.name,
    review_queue_item_count: Number(detail.item_count || 0),
    review_queue_label_count: asArray(detail.labels || detail.queue_labels)
      .length,
    review_queue_requires_review: Boolean(detail.requires_review),
    review_queue_fixture_mode: createMode,
  });
  return detail;
}

async function ensureTemporaryAnnotatorMember(
  client,
  queue,
  userId,
  cleanup,
  evidence,
) {
  const detail = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
  );
  const annotators = asArray(detail.annotators);
  if (
    annotators.some((annotator) => String(annotator.user_id) === String(userId))
  ) {
    evidence.push({
      temporary_annotator_membership: "already-present",
      queue_id: queue.id,
      user_id: userId,
    });
    return detail;
  }

  const originalAnnotatorIds = annotators
    .map((annotator) => annotator.user_id)
    .filter(Boolean);
  await client.patch(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
    {
      annotator_ids: [...originalAnnotatorIds, userId],
    },
  );
  cleanup.defer("remove temporary non-target review annotator", () =>
    client.patch(
      apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      {
        annotator_ids: originalAnnotatorIds,
      },
    ),
  );
  evidence.push({
    temporary_annotator_membership: "added",
    queue_id: queue.id,
    user_id: userId,
    original_member_count: originalAnnotatorIds.length,
  });
  return client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
  );
}

async function ensureExplicitQueueMember(
  client,
  queue,
  userId,
  cleanup,
  evidence,
  {
    reason = "api journey coverage",
    roles = ["manager", "reviewer", "annotator"],
  } = {},
) {
  const detail = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
  );
  const annotators = asArray(detail.annotators);
  const currentMember = annotators.find(
    (annotator) => String(annotator.user_id) === String(userId),
  );
  const currentRoles = currentMember
    ? asArray(currentMember.roles).length
      ? asArray(currentMember.roles)
      : [currentMember.role || "annotator"]
    : [];
  const hasDesiredRoles = roles.every((role) => currentRoles.includes(role));
  if (currentMember && hasDesiredRoles) {
    evidence.push({
      explicit_queue_membership: "already-present",
      queue_id: queue.id,
      user_id: userId,
      roles,
      reason,
    });
    return detail;
  }

  const originalAnnotatorIds = annotators
    .map((annotator) => annotator.user_id)
    .filter(Boolean);
  const originalRoles = {};
  for (const annotator of annotators) {
    if (!annotator.user_id) continue;
    const roles = asArray(annotator.roles);
    originalRoles[String(annotator.user_id)] = roles.length
      ? roles
      : [annotator.role || "annotator"];
  }
  const patchedAnnotatorIds = originalAnnotatorIds.some(
    (annotatorId) => String(annotatorId) === String(userId),
  )
    ? originalAnnotatorIds
    : [...originalAnnotatorIds, userId];
  await client.patch(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
    {
      annotator_ids: patchedAnnotatorIds,
      annotator_roles: {
        ...originalRoles,
        [String(userId)]: roles,
      },
    },
  );
  cleanup.defer("restore explicit queue membership", () =>
    client.patch(
      apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
      {
        annotator_ids: originalAnnotatorIds,
        annotator_roles: originalRoles,
      },
    ),
  );
  evidence.push({
    explicit_queue_membership: currentMember ? "roles-updated" : "added",
    queue_id: queue.id,
    user_id: userId,
    original_member_count: originalAnnotatorIds.length,
    roles,
    reason,
  });
  return client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queue.id }),
  );
}

function buildQueueAnnotatorRoleMap(annotators) {
  const roleMap = {};
  for (const annotator of asArray(annotators)) {
    if (!annotator?.user_id) continue;
    const roles = asArray(annotator.roles);
    roleMap[String(annotator.user_id)] = roles.length
      ? roles
      : [annotator.role || "annotator"];
  }
  return roleMap;
}

function findQueueAnnotator(queueDetail, userId) {
  return asArray(queueDetail?.annotators).find(
    (annotator) => String(annotator.user_id) === String(userId),
  );
}

async function restoreQueueAnnotators(client, queueId, annotatorIds, roles) {
  return client.patch(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
    {
      annotator_ids: annotatorIds,
      annotator_roles: roles,
    },
  );
}

async function resolveManagerQueueWithoutSource(
  client,
  sourceType,
  sourceId,
  sourceProjectId,
  evidence,
) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100, include_counts: true },
    }),
  );
  const candidates = [];
  for (const candidate of queues) {
    if (!candidate?.id || candidate.status !== "active") continue;
    const detail = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: candidate.id }),
    );
    const roles = asArray(detail.viewer_roles);
    if (!roles.includes("manager")) continue;
    const queueProjectId = relatedId(detail.project);
    if (
      queueProjectId &&
      sourceProjectId &&
      String(queueProjectId) !== String(sourceProjectId)
    ) {
      continue;
    }
    if (queueProjectId && !sourceProjectId) continue;
    const queueItems = asArray(
      await client.get(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", detail.id),
        {
          query: {
            limit: 500,
            status: ["pending", "in_progress", "completed", "skipped"],
          },
        },
      ),
    );
    if (
      queueItems.some(
        (item) =>
          String(item.source_type || item.sourceType) === String(sourceType) &&
          String(item.source_id || item.sourceId) === String(sourceId),
      )
    ) {
      continue;
    }
    const requiredLabelCount = asArray(
      detail.labels || detail.queue_labels || detail.annotation_labels,
    ).filter((label) => label.required).length;
    candidates.push({
      detail,
      itemCount: queueItems.length,
      requiredLabelCount,
      requiresReview: Boolean(detail.requires_review),
    });
  }
  candidates.sort(
    (left, right) =>
      Number(left.requiresReview) - Number(right.requiresReview) ||
      left.requiredLabelCount - right.requiredLabelCount ||
      left.itemCount - right.itemCount,
  );
  const selected = candidates[0];
  if (selected) {
    evidence.push({
      automation_queue_id: selected.detail.id,
      automation_queue_name: selected.detail.name,
      automation_queue_items_sampled: selected.itemCount,
      automation_queue_required_labels: selected.requiredLabelCount,
      automation_queue_requires_review: selected.requiresReview,
      automation_source_type: sourceType,
      automation_source_id: sourceId,
      automation_source_project_id: sourceProjectId,
    });
    return selected.detail;
  }
  skip(
    `No active manager-access queue is available without an active ${sourceType} item for ${sourceId}.`,
  );
}

async function resolveMemberRoleQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  runId,
  evidence,
) {
  const configuredQueueId = process.env.ANNOTATION_MEMBER_ROLE_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    const roles = asArray(queue.viewer_roles);
    if (!roles.includes("manager")) {
      skip("ANNOTATION_MEMBER_ROLE_QUEUE_ID is not manager-accessible.");
    }
    if (queue.status !== "active") {
      skip("ANNOTATION_MEMBER_ROLE_QUEUE_ID must point to an active queue.");
    }
    if (queue.auto_assign) {
      skip("ANNOTATION_MEMBER_ROLE_QUEUE_ID must not be auto-assign enabled.");
    }
    evidence.push({
      member_role_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
      queue_status: queue.status,
      queue_auto_assign: Boolean(queue.auto_assign),
    });
    return { queue, candidate: null };
  }

  const seededCandidate = await insertQueueMemberRoleFixtureDb({
    runId,
    organizationId,
    workspaceId,
    userId,
  });
  let queue;
  try {
    queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
  } catch (error) {
    await deleteQueueMemberRoleFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
  evidence.push({
    member_role_queue_source: "seeded-db-fixture",
    queue_id: seededCandidate.queue_id,
    queue_name: queue.name || seededCandidate.queue_name,
    queue_status: queue.status,
    queue_auto_assign: Boolean(queue.auto_assign),
    seeded_member_id: seededCandidate.seeded_member_id,
    seeded_initial_roles: seededCandidate.initial_roles,
  });
  return { queue, candidate: seededCandidate };
}

async function insertQueueMemberRoleFixtureDb({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const initialRoles = ["manager"];
  const queueName = `api_aq_member_roles_${runId}_${Date.now().toString(36)}`;
  const fixture = await insertAnnotationQueueCreateFixtureDb({
    queueName,
    organizationId,
    workspaceId,
    userId,
  });
  const sql = `
WITH updated_member AS (
  UPDATE model_hub_annotationqueueannotator
  SET
    role = 'manager',
    roles = ${sqlJson(initialRoles)},
    updated_at = now()
  WHERE id = ${sqlUuid(fixture.seeded_member_id, "memberId")}
    AND queue_id = ${sqlUuid(fixture.id, "queueId")}
    AND user_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
  RETURNING id::text, role, roles
)
SELECT json_build_object(
  'queue_id', ${sqlString(fixture.id)},
  'queue_name', ${sqlString(fixture.name)},
  'seeded_member_id', (SELECT id FROM updated_member),
  'initial_roles', (SELECT roles FROM updated_member),
  'seeded', true
)::text;
`;
  const updated = await runPostgresJson(sql);
  assert(
    updated?.seeded_member_id,
    `Failed to initialize AQ-API-029 member-role fixture: ${JSON.stringify(
      updated,
    )}.`,
  );
  return updated;
}

async function deleteQueueMemberRoleFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_member_roles_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
    AND (SELECT count(*) FROM deleted_item_notes) >= 0
    AND (SELECT count(*) FROM deleted_item_assignments) >= 0
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_rules) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_member_roles_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-029 member-role fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_item_notes', (SELECT count(*) FROM deleted_item_notes),
  'deleted_item_assignments', (SELECT count(*) FROM deleted_item_assignments),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_rules', (SELECT count(*) FROM deleted_rules),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function resolveManagerQueueForDatasetRows(client, datasetId, evidence) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100, include_counts: true },
    }),
  );
  const candidates = [];
  for (const candidate of queues) {
    if (!candidate?.id || candidate.status !== "active") continue;
    const detail = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: candidate.id }),
    );
    const roles = asArray(detail.viewer_roles);
    if (!roles.includes("manager")) continue;

    const queueDatasetId = relatedId(detail.dataset);
    const queueProjectId = relatedId(detail.project);
    const queueAgentDefinitionId = relatedId(
      detail.agent_definition || detail.agentDefinition,
    );
    if (queueDatasetId && String(queueDatasetId) !== String(datasetId))
      continue;
    if (!queueDatasetId && (queueProjectId || queueAgentDefinitionId)) continue;

    const queueItems = asArray(
      await client.get(
        queuePath("/model-hub/annotation-queues/{queue_id}/items/", detail.id),
        {
          query: {
            limit: 500,
            status: ["pending", "in_progress", "completed", "skipped"],
          },
        },
      ),
    );
    const requiredLabelCount = asArray(
      detail.labels || detail.queue_labels || detail.annotation_labels,
    ).filter((label) => label.required).length;
    candidates.push({
      detail,
      itemCount: queueItems.length,
      requiredLabelCount,
      requiresReview: Boolean(detail.requires_review),
      matchingDatasetScope: queueDatasetId
        ? String(queueDatasetId) === String(datasetId)
        : false,
    });
  }
  candidates.sort(
    (left, right) =>
      Number(right.matchingDatasetScope) - Number(left.matchingDatasetScope) ||
      Number(left.requiresReview) - Number(right.requiresReview) ||
      left.requiredLabelCount - right.requiredLabelCount ||
      left.itemCount - right.itemCount,
  );
  const selected = candidates[0];
  if (selected) {
    evidence.push({
      dataset_row_queue_id: selected.detail.id,
      dataset_row_queue_name: selected.detail.name,
      dataset_row_queue_items_sampled: selected.itemCount,
      dataset_row_queue_required_labels: selected.requiredLabelCount,
      dataset_row_queue_requires_review: selected.requiresReview,
      dataset_row_queue_matches_dataset_scope: selected.matchingDatasetScope,
      dataset_row_source_dataset_id: datasetId,
    });
    return selected.detail;
  }
  skip(
    "No active manager-access unscoped or matching-dataset queue is available for dataset-row add coverage.",
  );
}

async function resolveManagerQueue(client, evidence) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 100, include_counts: true },
    }),
  );
  const candidates = [];
  for (const candidate of queues) {
    if (!candidate?.id || candidate.status !== "active") continue;
    const detail = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: candidate.id }),
    );
    const roles = asArray(detail.viewer_roles);
    if (roles.includes("manager")) {
      const queueItems = asArray(
        await client.get(
          queuePath(
            "/model-hub/annotation-queues/{queue_id}/items/",
            detail.id,
          ),
          {
            query: {
              limit: 500,
              status: ["pending", "in_progress", "completed", "skipped"],
            },
          },
        ),
      );
      const openItems = queueItems.filter((item) =>
        ["pending", "in_progress", "skipped"].includes(item.status),
      );
      const completedItems = queueItems.filter(
        (item) => item.status === "completed",
      );
      const inProgressItems = queueItems.filter(
        (item) => item.status === "in_progress",
      );
      const rejectedOpenItems = openItems.filter(
        (item) => item.review_status === "rejected",
      );
      const requiredLabelCount = asArray(
        detail.labels || detail.queue_labels || detail.annotation_labels,
      ).filter((label) => label.required).length;
      candidates.push({
        detail,
        itemCount: queueItems.length,
        openCount: openItems.length,
        completedCount: completedItems.length,
        inProgressCount: inProgressItems.length,
        rejectedOpenCount: rejectedOpenItems.length,
        requiredLabelCount,
        requiresReview: Boolean(detail.requires_review),
      });
    }
  }
  candidates.sort(
    (left, right) =>
      Number(left.requiresReview) - Number(right.requiresReview) ||
      left.requiredLabelCount - right.requiredLabelCount ||
      left.completedCount - right.completedCount ||
      left.inProgressCount - right.inProgressCount ||
      left.rejectedOpenCount - right.rejectedOpenCount ||
      left.openCount - right.openCount ||
      left.itemCount - right.itemCount,
  );
  const selected = candidates[0];
  if (selected) {
    evidence.push({
      fallback_queue_id: selected.detail.id,
      fallback_queue_name: selected.detail.name,
      fallback_queue_status: selected.detail.status,
      fallback_queue_items_sampled: selected.itemCount,
      fallback_queue_open_items: selected.openCount,
      fallback_queue_completed_items: selected.completedCount,
      fallback_queue_in_progress_items: selected.inProgressCount,
      fallback_queue_rejected_open_items: selected.rejectedOpenCount,
      fallback_queue_required_labels: selected.requiredLabelCount,
      fallback_queue_requires_review: selected.requiresReview,
    });
    return selected.detail;
  }
  skip(
    "Queue create is entitlement-blocked and no active manager-access fallback queue is available.",
  );
}

async function resolveStatusTransitionQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  runId,
  evidence,
) {
  const configuredQueueId = process.env.ANNOTATION_STATUS_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    const roles = asArray(queue.viewer_roles);
    if (!roles.includes("manager")) {
      skip("ANNOTATION_STATUS_QUEUE_ID is not manager-accessible.");
    }
    if (queue.status !== "active") {
      skip("ANNOTATION_STATUS_QUEUE_ID must point to an active queue.");
    }
    evidence.push({
      status_transition_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
      queue_status: queue.status,
    });
    return { queue, candidate: null };
  }

  const seededCandidate = await insertStatusTransitionQueueFixtureDb({
    runId,
    organizationId,
    workspaceId,
    userId,
  });
  let queue;
  try {
    queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
  } catch (error) {
    await deleteStatusTransitionQueueFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
  evidence.push({
    status_transition_queue_source: "seeded-db-fixture",
    queue_id: seededCandidate.queue_id,
    queue_name: queue.name || seededCandidate.queue_name,
    queue_status: queue.status,
    seeded_member_id: seededCandidate.seeded_member_id,
  });
  return { queue, candidate: seededCandidate };
}

async function resolveQueueFullUpdateQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  runId,
  evidence,
) {
  const configuredQueueId =
    process.env.ANNOTATION_FULL_UPDATE_QUEUE_ID ||
    process.env.ANNOTATION_SETTINGS_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    const roles = asArray(queue.viewer_roles);
    if (!roles.includes("manager")) {
      skip(
        "ANNOTATION_FULL_UPDATE_QUEUE_ID/ANNOTATION_SETTINGS_QUEUE_ID is not manager-accessible.",
      );
    }
    evidence.push({
      full_update_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
      queue_status: queue.status || null,
    });
    return { queue, candidate: null };
  }

  const seededCandidate = await insertQueueFullUpdateFixtureDb({
    runId,
    organizationId,
    workspaceId,
    userId,
  });
  let queue;
  try {
    queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
  } catch (error) {
    await deleteQueueFullUpdateFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
  evidence.push({
    full_update_queue_source: "seeded-db-fixture",
    queue_id: seededCandidate.queue_id,
    queue_name: queue.name || seededCandidate.queue_name,
    queue_status: queue.status,
    seeded_member_id: seededCandidate.seeded_member_id,
  });
  return { queue, candidate: seededCandidate };
}

async function insertQueueFullUpdateFixtureDb({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const queueName = `api_aq_full_put_${runId}_${Date.now().toString(36)}`;
  const fixture = await insertAnnotationQueueCreateFixtureDb({
    queueName,
    organizationId,
    workspaceId,
    userId,
  });
  return {
    queue_id: fixture.id,
    queue_name: fixture.name,
    seeded_member_id: fixture.seeded_member_id,
    seeded: true,
  };
}

async function deleteQueueFullUpdateFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_full_put_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
    AND (SELECT count(*) FROM deleted_item_notes) >= 0
    AND (SELECT count(*) FROM deleted_item_assignments) >= 0
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_rules) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_full_put_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-027 full-update fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_item_notes', (SELECT count(*) FROM deleted_item_notes),
  'deleted_item_assignments', (SELECT count(*) FROM deleted_item_assignments),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_rules', (SELECT count(*) FROM deleted_rules),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function resolveQueueItemCrudQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  runId,
  evidence,
) {
  const configuredQueueId =
    process.env.ANNOTATION_ITEM_CRUD_QUEUE_ID ||
    process.env.ANNOTATION_ITEM_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    const roles = asArray(queue.viewer_roles);
    if (!roles.includes("manager")) {
      skip(
        "ANNOTATION_ITEM_CRUD_QUEUE_ID/ANNOTATION_ITEM_QUEUE_ID is not manager-accessible.",
      );
    }
    if (queue.status !== "active") {
      skip(
        "ANNOTATION_ITEM_CRUD_QUEUE_ID/ANNOTATION_ITEM_QUEUE_ID must point to an active queue.",
      );
    }
    evidence.push({
      item_crud_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
      queue_status: queue.status,
    });
    return { queue, candidate: null };
  }

  const seededCandidate = await insertQueueItemCrudFixtureDb({
    runId,
    organizationId,
    workspaceId,
    userId,
  });
  let queue;
  try {
    queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
  } catch (error) {
    await deleteQueueItemCrudFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
  evidence.push({
    item_crud_queue_source: "seeded-db-fixture",
    queue_id: seededCandidate.queue_id,
    queue_name: queue.name || seededCandidate.queue_name,
    queue_status: queue.status,
    seeded_member_id: seededCandidate.seeded_member_id,
  });
  return { queue, candidate: seededCandidate };
}

async function insertQueueItemCrudFixtureDb({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const queueName = `api_aq_item_crud_${runId}_${Date.now().toString(36)}`;
  const fixture = await insertAnnotationQueueCreateFixtureDb({
    queueName,
    organizationId,
    workspaceId,
    userId,
  });
  return {
    queue_id: fixture.id,
    queue_name: fixture.name,
    seeded_member_id: fixture.seeded_member_id,
    seeded: true,
  };
}

async function deleteQueueItemCrudFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_item_crud_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
    AND (SELECT count(*) FROM deleted_item_notes) >= 0
    AND (SELECT count(*) FROM deleted_item_assignments) >= 0
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_rules) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_item_crud_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-028 item CRUD fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_item_notes', (SELECT count(*) FROM deleted_item_notes),
  'deleted_item_assignments', (SELECT count(*) FROM deleted_item_assignments),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_rules', (SELECT count(*) FROM deleted_rules),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function insertStatusTransitionQueueFixtureDb({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  const queueName = `api_aq_status_${runId}_${Date.now().toString(36)}`;
  const fixture = await insertAnnotationQueueCreateFixtureDb({
    queueName,
    organizationId,
    workspaceId,
    userId,
  });
  return {
    queue_id: fixture.id,
    queue_name: fixture.name,
    seeded_member_id: fixture.seeded_member_id,
    seeded: true,
  };
}

async function deleteStatusTransitionQueueFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_status_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_item_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
    AND (SELECT count(*) FROM deleted_item_notes) >= 0
    AND (SELECT count(*) FROM deleted_item_assignments) >= 0
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_rules) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_status_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-026 status transition fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_item_notes', (SELECT count(*) FROM deleted_item_notes),
  'deleted_item_assignments', (SELECT count(*) FROM deleted_item_assignments),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_rules', (SELECT count(*) FROM deleted_rules),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function resolveMetricsQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  evidence,
) {
  const configuredQueueId = process.env.ANNOTATION_METRICS_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    evidence.push({
      metrics_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
    });
    return { queue, candidate: null };
  }

  const candidates = await loadMetricQueueCandidatesDb(
    organizationId,
    workspaceId,
  );
  for (const candidate of candidates) {
    try {
      const queue = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: candidate.queue_id,
        }),
      );
      evidence.push({
        metrics_queue_source: "db-candidate",
        queue_id: candidate.queue_id,
        queue_name: queue.name || candidate.queue_name,
        active_items: candidate.active_items,
        active_scores: candidate.active_scores,
        annotators: candidate.annotators,
        comparable_item_labels: candidate.comparable_item_labels,
      });
      return { queue, candidate };
    } catch (error) {
      if (![403, 404].includes(error.status)) throw error;
      evidence.push({
        metrics_queue_candidate_skipped: candidate.queue_id,
        status: error.status,
      });
    }
  }

  const seededCandidate = await insertMetricsQueueFixtureDb({
    organizationId,
    workspaceId,
    userId,
  });
  try {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
    evidence.push({
      metrics_queue_source: "seeded-db-fixture",
      queue_id: seededCandidate.queue_id,
      queue_name: queue.name || seededCandidate.queue_name,
      active_items: seededCandidate.active_items,
      active_scores: seededCandidate.active_scores,
      annotators: seededCandidate.annotators,
      comparable_item_labels: seededCandidate.comparable_item_labels,
    });
    return { queue, candidate: seededCandidate };
  } catch (error) {
    await deleteMetricsQueueFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
}

async function resolveExportFieldsQueue(
  client,
  organizationId,
  workspaceId,
  userId,
  evidence,
) {
  const configuredQueueId =
    process.env.ANNOTATION_EXPORT_FIELDS_QUEUE_ID ||
    process.env.ANNOTATION_METRICS_QUEUE_ID;
  if (configuredQueueId) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: configuredQueueId,
      }),
    );
    evidence.push({
      export_fields_queue_source: "env",
      queue_id: configuredQueueId,
      queue_name: queue.name || null,
    });
    return { queue, candidate: null };
  }

  const candidates = await loadExportFieldQueueCandidatesDb(
    organizationId,
    workspaceId,
  );
  for (const candidate of candidates) {
    try {
      const queue = await client.get(
        apiPath("/model-hub/annotation-queues/{id}/", {
          id: candidate.queue_id,
        }),
      );
      evidence.push({
        export_fields_queue_source: "db-candidate",
        queue_id: candidate.queue_id,
        queue_name: queue.name || candidate.queue_name,
        active_items: candidate.active_items,
        labels: candidate.labels,
        source_types: candidate.source_types,
        source_type_list: candidate.source_type_list,
      });
      return { queue, candidate };
    } catch (error) {
      if (![403, 404].includes(error.status)) throw error;
      evidence.push({
        export_fields_queue_candidate_skipped: candidate.queue_id,
        status: error.status,
      });
    }
  }

  const seededCandidate = await insertExportFieldsQueueFixtureDb({
    organizationId,
    workspaceId,
    userId,
  });
  try {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: seededCandidate.queue_id,
      }),
    );
    evidence.push({
      export_fields_queue_source: "seeded-db-fixture",
      queue_id: seededCandidate.queue_id,
      queue_name: queue.name || seededCandidate.queue_name,
      active_items: seededCandidate.active_items,
      labels: seededCandidate.labels,
      source_types: seededCandidate.source_types,
      source_type_list: seededCandidate.source_type_list,
    });
    return { queue, candidate: seededCandidate };
  } catch (error) {
    await deleteExportFieldsQueueFixtureDb(seededCandidate.queue_id).catch(
      () => null,
    );
    throw error;
  }
}

async function loadMetricQueueCandidatesDb(organizationId, workspaceId) {
  const sql = `
with score_counts as (
  select
    qi.queue_id,
    count(s.id) as active_scores,
    count(distinct s.annotator_id) filter (where s.annotator_id is not null) as annotators
  from model_hub_queueitem qi
  join model_hub_score s on s.queue_item_id = qi.id
  where s.deleted = false
  group by qi.queue_id
),
comparable as (
  select
    queue_id,
    count(*) as comparable_item_labels
  from (
    select qi.queue_id, s.queue_item_id, s.label_id
    from model_hub_score s
    join model_hub_queueitem qi on qi.id = s.queue_item_id
    where s.deleted = false and qi.deleted = false
    group by qi.queue_id, s.queue_item_id, s.label_id
    having count(*) >= 2
  ) grouped
  group by queue_id
),
ranked as (
  select
    q.id::text as queue_id,
    q.name as queue_name,
    count(distinct qi.id) filter (where qi.deleted = false) as active_items,
    coalesce(sc.active_scores, 0) as active_scores,
    coalesce(sc.annotators, 0) as annotators,
    coalesce(c.comparable_item_labels, 0) as comparable_item_labels
  from model_hub_annotationqueue q
  left join model_hub_queueitem qi on qi.queue_id = q.id
  left join score_counts sc on sc.queue_id = q.id
  left join comparable c on c.queue_id = q.id
  where
    q.deleted = false
    and q.status = 'active'
    and q.organization_id = ${sqlUuid(organizationId, "organizationId")}
    and q.workspace_id = ${sqlUuid(workspaceId, "workspaceId")}
  group by q.id, q.name, sc.active_scores, sc.annotators, c.comparable_item_labels
  having count(distinct qi.id) filter (where qi.deleted = false) > 0
  order by
    coalesce(c.comparable_item_labels, 0) desc,
    coalesce(sc.active_scores, 0) desc,
    count(distinct qi.id) filter (where qi.deleted = false) desc
  limit 10
)
select coalesce(json_agg(row_to_json(ranked)), '[]'::json)::text from ranked;
`;
  return asArray(await runPostgresJson(sql));
}

async function insertMetricsQueueFixtureDb({
  organizationId,
  workspaceId,
  userId,
}) {
  const queueId = randomUUID();
  const labelId = randomUUID();
  const queueLabelId = randomUUID();
  const memberId = randomUUID();
  const itemOneId = randomUUID();
  const itemTwoId = randomUUID();
  const scoreOneId = randomUUID();
  const scoreTwoId = randomUUID();
  const runId = Date.now().toString(36);
  const queueName = `api_aq_metrics_${runId}`;
  const labelName = `api_aq_metrics_label_${runId}`;
  const labelSettings = {
    rule_prompt: "",
    multi_choice: false,
    options: [{ label: "accurate" }, { label: "needs review" }],
    auto_annotate: false,
    strategy: "manual",
  };
  const metadata = {
    journey: "AQ-API-024",
    fixture: "metrics-db-audit",
    run_id: runId,
  };

  const sql = `
WITH inserted_label AS (
  INSERT INTO model_hub_annotationslabels (
    created_at, updated_at, deleted, deleted_at, id, name, type, settings,
    description, organization_id, project_id, workspace_id, metadata, allow_notes
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(labelId, "labelId")},
    ${sqlString(labelName)},
    'categorical',
    ${sqlJson(labelSettings)},
    ${sqlString("Disposable label for AQ-API-024 metrics coverage.")},
    ${sqlUuid(organizationId, "organizationId")},
    NULL,
    ${sqlUuid(workspaceId, "workspaceId")},
    ${sqlJson(metadata)},
    false
  )
  RETURNING id
),
inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at, updated_at, deleted, deleted_at, id, name, description,
    instructions, status, assignment_strategy, annotations_required,
    reservation_timeout_minutes, requires_review, created_by_id,
    organization_id, workspace_id, project_id, is_default, dataset_id,
    agent_definition_id, auto_assign
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(queueId, "queueId")},
    ${sqlString(queueName)},
    ${sqlString("Disposable queue for AQ-API-024 metrics coverage.")},
    ${sqlString("Verify progress, analytics, and agreement DB formulas.")},
    'active',
    'manual',
    2,
    60,
    false,
    ${sqlUuid(userId, "userId")},
    ${sqlUuid(organizationId, "organizationId")},
    ${sqlUuid(workspaceId, "workspaceId")},
    NULL,
    false,
    NULL,
    NULL,
    true
  )
  RETURNING id, name
),
inserted_queue_label AS (
  INSERT INTO model_hub_annotationqueuelabel (
    created_at, updated_at, deleted, deleted_at, id, required, "order", label_id, queue_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(queueLabelId, "queueLabelId")},
    true,
    0,
    inserted_label.id,
    inserted_queue.id
  FROM inserted_label, inserted_queue
  RETURNING id
),
inserted_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id, user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(memberId, "memberId")},
    'manager',
    ${sqlJson(["manager", "reviewer", "annotator"])},
    inserted_queue.id,
    ${sqlUuid(userId, "userId")}
  FROM inserted_queue
  RETURNING id
),
inserted_items AS (
  INSERT INTO model_hub_queueitem (
    created_at, updated_at, deleted, deleted_at, id, source_type, status,
    priority, "order", metadata, reserved_at, reservation_expires_at,
    review_status, reviewed_at, review_notes, assigned_to_id, call_execution_id,
    dataset_row_id, observation_span_id, organization_id, prototype_run_id,
    queue_id, reserved_by_id, reviewed_by_id, trace_id, workspace_id,
    trace_session_id
  )
  VALUES
    (
      now(), now(), false, NULL,
      ${sqlUuid(itemOneId, "itemOneId")},
      'trace',
      'completed',
      10,
      0,
      ${sqlJson({ ...metadata, item: "completed" })},
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      ${sqlUuid(queueId, "queueId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL
    ),
    (
      now(), now(), false, NULL,
      ${sqlUuid(itemTwoId, "itemTwoId")},
      'trace',
      'pending',
      0,
      1,
      ${sqlJson({ ...metadata, item: "pending" })},
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      ${sqlUuid(queueId, "queueId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL
    )
  RETURNING id
),
inserted_scores AS (
  INSERT INTO model_hub_score (
    created_at, updated_at, deleted, deleted_at, id, source_type, value,
    score_source, notes, annotator_id, call_execution_id, dataset_row_id,
    label_id, observation_span_id, organization_id, project_id, prototype_run_id,
    queue_item_id, trace_id, trace_session_id, workspace_id, value_history
  )
  VALUES
    (
      now(), now(), false, NULL,
      ${sqlUuid(scoreOneId, "scoreOneId")},
      'trace',
      ${sqlJson("accurate")},
      'human',
      ${sqlString("Synthetic matching score for AQ-API-024.")},
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      ${sqlUuid(labelId, "labelId")},
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      NULL,
      ${sqlUuid(itemOneId, "itemOneId")},
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      '[]'::jsonb
    ),
    (
      now(), now(), false, NULL,
      ${sqlUuid(scoreTwoId, "scoreTwoId")},
      'trace',
      ${sqlJson("accurate")},
      'human',
      ${sqlString("Synthetic matching score for AQ-API-024.")},
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      ${sqlUuid(labelId, "labelId")},
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      NULL,
      ${sqlUuid(itemOneId, "itemOneId")},
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      '[]'::jsonb
    )
  RETURNING id
)
SELECT json_build_object(
  'queue_id', ${sqlString(queueId)},
  'queue_name', ${sqlString(queueName)},
  'label_id', ${sqlString(labelId)},
  'active_items', (SELECT count(*) FROM inserted_items),
  'active_scores', (SELECT count(*) FROM inserted_scores),
  'annotators', 1,
  'comparable_item_labels', 1,
  'seeded', true
)::text;
`;
  return runPostgresJson(sql);
}

async function deleteMetricsQueueFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_metrics_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_metrics_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-024 metrics fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadExportFieldQueueCandidatesDb(organizationId, workspaceId) {
  const sql = `
with ranked as (
  select
    q.id::text as queue_id,
    q.name as queue_name,
    count(distinct qi.source_type) filter (where qi.deleted = false) as source_types,
    string_agg(distinct qi.source_type, ',') filter (where qi.deleted = false) as source_type_list,
    count(distinct ql.label_id) filter (where ql.deleted = false) as labels,
    count(distinct qi.id) filter (where qi.deleted = false) as active_items
  from model_hub_annotationqueue q
  left join model_hub_queueitem qi on qi.queue_id = q.id
  left join model_hub_annotationqueuelabel ql on ql.queue_id = q.id
  where
    q.deleted = false
    and q.status = 'active'
    and q.organization_id = ${sqlUuid(organizationId, "organizationId")}
    and q.workspace_id = ${sqlUuid(workspaceId, "workspaceId")}
  group by q.id, q.name
  having
    count(distinct ql.label_id) filter (where ql.deleted = false) > 0
    and count(distinct qi.id) filter (where qi.deleted = false) > 0
  order by
    count(distinct qi.source_type) filter (where qi.deleted = false) desc,
    count(distinct ql.label_id) filter (where ql.deleted = false) desc,
    count(distinct qi.id) filter (where qi.deleted = false) desc
  limit 10
)
select coalesce(json_agg(row_to_json(ranked)), '[]'::json)::text from ranked;
`;
  return asArray(await runPostgresJson(sql));
}

async function insertExportFieldsQueueFixtureDb({
  organizationId,
  workspaceId,
  userId,
}) {
  const queueId = randomUUID();
  const labelId = randomUUID();
  const queueLabelId = randomUUID();
  const memberId = randomUUID();
  const traceItemId = randomUUID();
  const sessionItemId = randomUUID();
  const scoreOneId = randomUUID();
  const scoreTwoId = randomUUID();
  const runId = Date.now().toString(36);
  const queueName = `api_aq_export_fields_${runId}`;
  const labelName = `api_aq_export_fields_label_${runId}`;
  const labelSettings = {
    rule_prompt: "",
    multi_choice: false,
    options: [{ label: "accurate" }, { label: "needs review" }],
    auto_annotate: false,
    strategy: "manual",
  };
  const metadata = {
    journey: "AQ-API-025",
    fixture: "export-fields-db-audit",
    run_id: runId,
  };

  const sql = `
WITH inserted_label AS (
  INSERT INTO model_hub_annotationslabels (
    created_at, updated_at, deleted, deleted_at, id, name, type, settings,
    description, organization_id, project_id, workspace_id, metadata, allow_notes
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(labelId, "labelId")},
    ${sqlString(labelName)},
    'categorical',
    ${sqlJson(labelSettings)},
    ${sqlString("Disposable label for AQ-API-025 export field coverage.")},
    ${sqlUuid(organizationId, "organizationId")},
    NULL,
    ${sqlUuid(workspaceId, "workspaceId")},
    ${sqlJson(metadata)},
    true
  )
  RETURNING id
),
inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at, updated_at, deleted, deleted_at, id, name, description,
    instructions, status, assignment_strategy, annotations_required,
    reservation_timeout_minutes, requires_review, created_by_id,
    organization_id, workspace_id, project_id, is_default, dataset_id,
    agent_definition_id, auto_assign
  )
  VALUES (
    now(), now(), false, NULL,
    ${sqlUuid(queueId, "queueId")},
    ${sqlString(queueName)},
    ${sqlString("Disposable queue for AQ-API-025 export field coverage.")},
    ${sqlString("Verify export fields, default mappings, and annotation slots.")},
    'active',
    'manual',
    2,
    60,
    false,
    ${sqlUuid(userId, "userId")},
    ${sqlUuid(organizationId, "organizationId")},
    ${sqlUuid(workspaceId, "workspaceId")},
    NULL,
    false,
    NULL,
    NULL,
    true
  )
  RETURNING id, name
),
inserted_queue_label AS (
  INSERT INTO model_hub_annotationqueuelabel (
    created_at, updated_at, deleted, deleted_at, id, required, "order", label_id, queue_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(queueLabelId, "queueLabelId")},
    true,
    0,
    inserted_label.id,
    inserted_queue.id
  FROM inserted_label, inserted_queue
  RETURNING id
),
inserted_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at, updated_at, deleted, deleted_at, id, role, roles, queue_id, user_id
  )
  SELECT
    now(), now(), false, NULL,
    ${sqlUuid(memberId, "memberId")},
    'manager',
    ${sqlJson(["manager", "reviewer", "annotator"])},
    inserted_queue.id,
    ${sqlUuid(userId, "userId")}
  FROM inserted_queue
  RETURNING id
),
inserted_items AS (
  INSERT INTO model_hub_queueitem (
    created_at, updated_at, deleted, deleted_at, id, source_type, status,
    priority, "order", metadata, reserved_at, reservation_expires_at,
    review_status, reviewed_at, review_notes, assigned_to_id, call_execution_id,
    dataset_row_id, observation_span_id, organization_id, prototype_run_id,
    queue_id, reserved_by_id, reviewed_by_id, trace_id, workspace_id,
    trace_session_id
  )
  VALUES
    (
      now(), now(), false, NULL,
      ${sqlUuid(traceItemId, "traceItemId")},
      'trace',
      'completed',
      5,
      0,
      ${sqlJson({ ...metadata, item: "trace" })},
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      ${sqlUuid(queueId, "queueId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL
    ),
    (
      now(), now(), false, NULL,
      ${sqlUuid(sessionItemId, "sessionItemId")},
      'trace_session',
      'pending',
      1,
      1,
      ${sqlJson({ ...metadata, item: "trace_session" })},
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      ${sqlUuid(queueId, "queueId")},
      NULL,
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL
    )
  RETURNING id
),
inserted_scores AS (
  INSERT INTO model_hub_score (
    created_at, updated_at, deleted, deleted_at, id, source_type, value,
    score_source, notes, annotator_id, call_execution_id, dataset_row_id,
    label_id, observation_span_id, organization_id, project_id, prototype_run_id,
    queue_item_id, trace_id, trace_session_id, workspace_id, value_history
  )
  VALUES
    (
      now(), now(), false, NULL,
      ${sqlUuid(scoreOneId, "scoreOneId")},
      'trace',
      ${sqlJson("accurate")},
      'human',
      ${sqlString("Synthetic export-field score 1.")},
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      ${sqlUuid(labelId, "labelId")},
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      NULL,
      ${sqlUuid(traceItemId, "traceItemId")},
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      '[]'::jsonb
    ),
    (
      now(), now(), false, NULL,
      ${sqlUuid(scoreTwoId, "scoreTwoId")},
      'trace',
      ${sqlJson("needs review")},
      'human',
      ${sqlString("Synthetic export-field score 2.")},
      ${sqlUuid(userId, "userId")},
      NULL,
      NULL,
      ${sqlUuid(labelId, "labelId")},
      NULL,
      ${sqlUuid(organizationId, "organizationId")},
      NULL,
      NULL,
      ${sqlUuid(traceItemId, "traceItemId")},
      NULL,
      NULL,
      ${sqlUuid(workspaceId, "workspaceId")},
      '[]'::jsonb
    )
  RETURNING id
)
SELECT json_build_object(
  'queue_id', ${sqlString(queueId)},
  'queue_name', ${sqlString(queueName)},
  'label_id', ${sqlString(labelId)},
  'active_items', (SELECT count(*) FROM inserted_items),
  'labels', 1,
  'source_types', 2,
  'source_type_list', 'trace,trace_session',
  'seeded', true
)::text;
`;
  return runPostgresJson(sql);
}

async function deleteExportFieldsQueueFixtureDb(queueId) {
  const sql = `
WITH target_queue AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
    AND name LIKE 'api_aq_export_fields_%'
),
target_items AS (
  SELECT id
  FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
),
target_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM target_items)
  RETURNING id
),
deleted_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM target_queue)
  RETURNING id
),
deleted_items AS (
  DELETE FROM model_hub_queueitem
  WHERE queue_id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_scores) >= 0
  RETURNING id
),
deleted_queue AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM target_queue)
    AND (SELECT count(*) FROM deleted_items) >= 0
    AND (SELECT count(*) FROM deleted_members) >= 0
    AND (SELECT count(*) FROM deleted_queue_labels) >= 0
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT label_id FROM target_labels)
    AND name LIKE 'api_aq_export_fields_label_%'
    AND (SELECT count(*) FROM deleted_queue) >= 0
  RETURNING id
)
SELECT json_build_object(
  'cleanup', 'hard delete AQ-API-025 export fields fixture',
  'status', 'passed',
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_items', (SELECT count(*) FROM deleted_items),
  'deleted_members', (SELECT count(*) FROM deleted_members),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_queues', (SELECT count(*) FROM deleted_queue)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadQueueMetricsDbAudit(queueId, currentUserIdValue) {
  const sql = `
with items as (
  select
    qi.id,
    qi.status,
    qi.review_status,
    qi.assigned_to_id,
    assigned.name as assigned_to_name,
    qi.updated_at
  from model_hub_queueitem qi
  left join accounts_user assigned on assigned.id = qi.assigned_to_id
  where qi.queue_id = ${sqlUuid(queueId, "queueId")} and qi.deleted = false
),
scores as (
  select
    s.id,
    s.queue_item_id,
    s.label_id,
    label.name as label_name,
    label.type as label_type,
    s.annotator_id,
    annotator.name as annotator_name,
    s.value,
    s.created_at
  from model_hub_score s
  join model_hub_queueitem qi on qi.id = s.queue_item_id
  left join model_hub_annotationslabels label on label.id = s.label_id
  left join accounts_user annotator on annotator.id = s.annotator_id
  where qi.queue_id = ${sqlUuid(queueId, "queueId")} and s.deleted = false
),
payload as (
  select json_build_object(
    'items',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'id', items.id::text,
              'status', items.status,
              'review_status', items.review_status,
              'assigned_to', items.assigned_to_id::text,
              'assigned_to_name', items.assigned_to_name,
              'updated_at', items.updated_at
            )
            order by items.id
          )
          from items
        ),
        '[]'::json
      ),
    'assignments',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'queue_item_id', a.queue_item_id::text,
              'user_id', a.user_id::text
            )
            order by a.queue_item_id, a.user_id
          )
          from model_hub_queueitemassignment a
          join items on items.id = a.queue_item_id
          where a.deleted = false
        ),
        '[]'::json
      ),
    'addressed_review_item_ids',
      coalesce(
        (
          select json_agg(distinct t.queue_item_id::text)
          from model_hub_queueitemreviewthread t
          join items on items.id = t.queue_item_id
          where
            t.deleted = false
            and t.blocking = true
            and t.status = 'addressed'
        ),
        '[]'::json
      ),
    'scores',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'id', scores.id::text,
              'queue_item_id', scores.queue_item_id::text,
              'label_id', scores.label_id::text,
              'label_name', scores.label_name,
              'label_type', scores.label_type,
              'annotator_id', scores.annotator_id::text,
              'annotator_name', scores.annotator_name,
              'value', scores.value,
              'created_at', scores.created_at
            )
            order by scores.queue_item_id, scores.label_id, scores.annotator_id
          )
          from scores
        ),
        '[]'::json
      ),
    'current_user_id', ${sqlUuid(currentUserIdValue, "currentUserId")}::text
  ) as data
)
select data::text from payload;
`;
  return runPostgresJson(sql);
}

async function loadQueueExportFieldsDbAudit(queueId) {
  const sql = `
with queue as (
  select id, annotations_required
  from model_hub_annotationqueue
  where id = ${sqlUuid(queueId, "queueId")}
),
sample_items as (
  select qi.id, qi.source_type, qi."order", qi.created_at
  from model_hub_queueitem qi
  where qi.queue_id = ${sqlUuid(queueId, "queueId")} and qi.deleted = false
  order by qi."order", qi.created_at
  limit 100
),
labels as (
  select
    ql.label_id,
    label.name,
    label.type,
    ql.required,
    ql."order",
    ql.created_at
  from model_hub_annotationqueuelabel ql
  join model_hub_annotationslabels label on label.id = ql.label_id
  where ql.queue_id = ${sqlUuid(queueId, "queueId")} and ql.deleted = false
  order by ql."order", ql.created_at
),
score_counts as (
  select
    s.queue_item_id,
    s.label_id,
    count(*) as score_count
  from model_hub_score s
  join sample_items on sample_items.id = s.queue_item_id
  where s.deleted = false
  group by s.queue_item_id, s.label_id
),
payload as (
  select json_build_object(
    'queue',
      (
        select json_build_object(
          'id', queue.id::text,
          'annotations_required', queue.annotations_required
        )
        from queue
      ),
    'sample_items',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'id', sample_items.id::text,
              'source_type', sample_items.source_type
            )
            order by sample_items."order", sample_items.created_at
          )
          from sample_items
        ),
        '[]'::json
      ),
    'labels',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'label_id', labels.label_id::text,
              'name', labels.name,
              'type', labels.type,
              'required', labels.required
            )
            order by labels."order", labels.created_at
          )
          from labels
        ),
        '[]'::json
      ),
    'score_counts',
      coalesce(
        (
          select json_agg(
            json_build_object(
              'queue_item_id', score_counts.queue_item_id::text,
              'label_id', score_counts.label_id::text,
              'score_count', score_counts.score_count
            )
            order by score_counts.queue_item_id, score_counts.label_id
          )
          from score_counts
        ),
        '[]'::json
      )
  ) as data
)
select data::text from payload;
`;
  return runPostgresJson(sql);
}

async function loadQueueStatusDbAudit(queueId) {
  const sql = `
with queue as (
  select
    id::text as id,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    auto_assign,
    deleted,
    organization_id::text as organization_id,
    workspace_id::text as workspace_id,
    updated_at,
    (
      select count(*)
      from model_hub_annotationqueuelabel ql
      where ql.queue_id = model_hub_annotationqueue.id and ql.deleted = false
    ) as active_label_count,
    (
      select count(*)
      from model_hub_annotationqueueannotator qa
      where qa.queue_id = model_hub_annotationqueue.id and qa.deleted = false
    ) as active_annotator_count
  from model_hub_annotationqueue
  where id = ${sqlUuid(queueId, "queueId")}
)
select coalesce(
  (
    select json_build_object(
      'id', queue.id,
      'name', queue.name,
      'description', queue.description,
      'instructions', queue.instructions,
      'status', queue.status,
      'assignment_strategy', queue.assignment_strategy,
      'annotations_required', queue.annotations_required,
      'reservation_timeout_minutes', queue.reservation_timeout_minutes,
      'requires_review', queue.requires_review,
      'auto_assign', queue.auto_assign,
      'deleted', queue.deleted,
      'organization_id', queue.organization_id,
      'workspace_id', queue.workspace_id,
      'updated_at', queue.updated_at,
      'active_label_count', queue.active_label_count,
      'active_annotator_count', queue.active_annotator_count
    )
    from queue
  ),
  '{}'::json
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.id, `Queue ${queueId} was not found in DB status audit.`);
  assert(audit.deleted === false, `Queue ${queueId} is deleted in DB audit.`);
  return audit;
}

async function loadQueueMemberDbAudit(queueId, userId) {
  const sql = `
with queue as (
  select
    q.id::text as id,
    q.name,
    q.organization_id::text as organization_id,
    q.workspace_id::text as workspace_id
  from model_hub_annotationqueue q
  where q.id = ${sqlUuid(queueId, "queueId")}
),
member as (
  select
    a.id::text as id,
    a.queue_id::text as queue_id,
    a.user_id::text as user_id,
    a.role,
    a.roles,
    a.deleted,
    a.deleted_at,
    a.updated_at
  from model_hub_annotationqueueannotator a
  where
    a.queue_id = ${sqlUuid(queueId, "queueId")}
    and a.user_id = ${sqlUuid(userId, "userId")}
    and a.deleted = false
  order by a.updated_at desc
  limit 1
)
select coalesce(
  (
    select json_build_object(
      'queue_id', queue.id,
      'queue_name', queue.name,
      'organization_id', queue.organization_id,
      'workspace_id', queue.workspace_id,
      'active_member_count',
        (
          select count(*)
          from model_hub_annotationqueueannotator active
          where active.queue_id = queue.id::uuid and active.deleted = false
        ),
      'active_user_rows',
        (
          select count(*)
          from model_hub_annotationqueueannotator active
          where
            active.queue_id = queue.id::uuid
            and active.user_id = ${sqlUuid(userId, "userId")}
            and active.deleted = false
        ),
      'soft_deleted_user_rows',
        (
          select count(*)
          from model_hub_annotationqueueannotator deleted_member
          where
            deleted_member.queue_id = queue.id::uuid
            and deleted_member.user_id = ${sqlUuid(userId, "userId")}
            and deleted_member.deleted = true
        ),
      'member',
        coalesce((select row_to_json(member) from member), '{}'::json)
    )
    from queue
  ),
  '{}'::json
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.queue_id, `Queue ${queueId} was not found in member DB audit.`);
  return audit;
}

async function loadAutomationRuleDbAudit(ruleId) {
  const sql = `
with rule as (
  select
    r.id::text as id,
    r.name,
    r.source_type,
    r.conditions,
    r.enabled,
    r.trigger_frequency,
    r.deleted,
    r.deleted_at,
    r.updated_at,
    r.last_triggered_at,
    r.trigger_count,
    r.created_by_id::text as created_by_id,
    r.organization_id::text as organization_id,
    r.queue_id::text as queue_id,
    q.workspace_id::text as queue_workspace_id
  from model_hub_automationrule r
  join model_hub_annotationqueue q on q.id = r.queue_id
  where r.id = ${sqlUuid(ruleId, "ruleId")}
)
select coalesce(
  (
    select json_build_object(
      'id', rule.id,
      'name', rule.name,
      'source_type', rule.source_type,
      'conditions', rule.conditions,
      'enabled', rule.enabled,
      'trigger_frequency', rule.trigger_frequency,
      'deleted', rule.deleted,
      'deleted_at', rule.deleted_at,
      'updated_at', rule.updated_at,
      'last_triggered_at', rule.last_triggered_at,
      'trigger_count', rule.trigger_count,
      'created_by_id', rule.created_by_id,
      'organization_id', rule.organization_id,
      'queue_id', rule.queue_id,
      'queue_workspace_id', rule.queue_workspace_id
    )
    from rule
  ),
  '{}'::json
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.id, `Automation rule ${ruleId} was not found in DB audit.`);
  return audit;
}

async function loadAutomationScheduleResidueDbAudit(namePrefix) {
  const sql = `
with matching_queues as (
  select id
  from model_hub_annotationqueue
  where name like ${sqlString(`${namePrefix}%`)}
),
matching_rules as (
  select id, deleted
  from model_hub_automationrule
  where name like ${sqlString(`${namePrefix}%`)}
     or queue_id in (select id from matching_queues)
)
select json_build_object(
  'matching_queues', (select count(*)::int from matching_queues),
  'matching_rules', (select count(*)::int from matching_rules),
  'matching_active_rules',
    (select count(*)::int from matching_rules where deleted = false)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadDiscussionDbAudit(threadId) {
  const sql = `
with thread as (
  select
    t.id::text as id,
    t.queue_item_id::text as queue_item_id,
    t.created_by_id::text as created_by_id,
    t.action,
    t.scope,
    t.status,
    t.blocking,
    t.deleted,
    t.deleted_at,
    t.resolved_by_id::text as resolved_by_id,
    t.resolved_at,
    t.reopened_by_id::text as reopened_by_id,
    t.reopened_at,
    t.organization_id::text as organization_id,
    t.workspace_id::text as workspace_id
  from model_hub_queueitemreviewthread t
  where t.id = ${sqlUuid(threadId, "threadId")}
),
comments as (
  select
    c.id::text as id,
    c.thread_id::text as thread_id,
    c.queue_item_id::text as queue_item_id,
    c.reviewer_id::text as reviewer_id,
    c.action,
    c.comment,
    c.reactions,
    c.deleted,
    c.deleted_at,
    c.organization_id::text as organization_id,
    c.workspace_id::text as workspace_id,
    coalesce(
      (
        select json_agg(m.user_id::text order by m.user_id::text)
        from model_hub_queueitemreviewcomment_mentioned_users m
        where m.queueitemreviewcomment_id = c.id
      ),
      '[]'::json
    ) as mentioned_user_ids
  from model_hub_queueitemreviewcomment c
  where c.thread_id = ${sqlUuid(threadId, "threadId")}
)
select json_build_object(
  'thread',
    coalesce((select row_to_json(thread) from thread), '{}'::json),
  'comments',
    coalesce(
      (
        select json_agg(row_to_json(comments) order by comments.id)
        from comments
      ),
      '[]'::json
    )
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.thread?.id, `Discussion thread ${threadId} was not found.`);
  return audit;
}

function assertDiscussionDbState(dbAudit, expected) {
  const thread = dbAudit.thread;
  const comments = asArray(dbAudit.comments);
  assert(
    String(thread.queue_item_id) === String(expected.queueItemId) &&
      String(thread.organization_id) === String(expected.organizationId) &&
      String(thread.workspace_id) === String(expected.workspaceId),
    `Discussion thread DB scope mismatch: ${JSON.stringify(thread)}.`,
  );
  assert(
    thread.action === "comment" &&
      thread.scope === "item" &&
      thread.blocking === false &&
      thread.status === expected.threadStatus,
    `Discussion thread DB state mismatch: ${JSON.stringify(thread)}.`,
  );
  assert(
    thread.deleted === expected.expectedDeleted,
    `Discussion thread deleted flag mismatch: ${JSON.stringify(thread)}.`,
  );

  const byId = new Map(
    comments.map((comment) => [String(comment.id), comment]),
  );
  const root = byId.get(String(expected.rootCommentId));
  const reply = byId.get(String(expected.replyCommentId));
  const resolve = expected.resolveCommentId
    ? byId.get(String(expected.resolveCommentId))
    : comments.find((comment) => comment.action === "resolve");
  const reopen = expected.reopenCommentId
    ? byId.get(String(expected.reopenCommentId))
    : comments.find((comment) => comment.action === "reopen");
  assert(
    root && reply && resolve && reopen,
    "Discussion DB audit missed rows.",
  );
  for (const comment of [root, reply, resolve, reopen]) {
    assert(
      String(comment.queue_item_id) === String(expected.queueItemId) &&
        String(comment.organization_id) === String(expected.organizationId) &&
        String(comment.workspace_id) === String(expected.workspaceId),
      `Discussion comment DB scope mismatch: ${JSON.stringify(comment)}.`,
    );
    assert(
      comment.deleted === expected.expectedDeleted,
      `Discussion comment deleted flag mismatch: ${JSON.stringify(comment)}.`,
    );
  }
  assert(
    root.action === "comment" &&
      reply.action === "comment" &&
      resolve.action === "resolve" &&
      reopen.action === "reopen",
    `Discussion comment actions mismatch: ${JSON.stringify(comments)}.`,
  );
  assert(
    asArray(root.mentioned_user_ids).some(
      (userId) => String(userId) === String(expected.userId),
    ),
    `Discussion root comment mention not persisted: ${JSON.stringify(root)}.`,
  );
  const reactionUserIds = Array.isArray(root.reactions?.[expected.emoji])
    ? root.reactions[expected.emoji]
    : [];
  assert(
    reactionUserIds.some(
      (userId) => String(userId) === String(expected.userId),
    ),
    `Discussion reaction not persisted: ${JSON.stringify(root.reactions)}.`,
  );
}

async function loadQueueItemDbAudit(itemId) {
  const sql = `
with item as (
  select
    qi.id::text as id,
    qi.queue_id::text as queue_id,
    qi.source_type,
    qi.status,
    qi.review_status,
    qi.review_notes,
    qi.priority,
    qi."order",
    qi.metadata,
    qi.assigned_to_id::text as assigned_to_id,
    qi.deleted,
    qi.deleted_at,
    qi.organization_id::text as organization_id,
    qi.workspace_id::text as workspace_id,
    qi.dataset_row_id::text as dataset_row_id,
    qi.trace_id::text as trace_id,
    qi.observation_span_id::text as observation_span_id,
    qi.prototype_run_id::text as prototype_run_id,
    qi.call_execution_id::text as call_execution_id,
    qi.trace_session_id::text as trace_session_id,
    (
      (case when qi.dataset_row_id is null then 0 else 1 end) +
      (case when qi.trace_id is null then 0 else 1 end) +
      (case when qi.observation_span_id is null then 0 else 1 end) +
      (case when qi.prototype_run_id is null then 0 else 1 end) +
      (case when qi.call_execution_id is null then 0 else 1 end) +
      (case when qi.trace_session_id is null then 0 else 1 end)
    ) as source_fk_count,
    (
      select count(*)
      from model_hub_score s
      where s.queue_item_id = qi.id and s.deleted = false
    ) as active_scores,
    (
      select count(*)
      from model_hub_queueitemnote n
      where n.queue_item_id = qi.id and n.deleted = false
    ) as active_notes,
    (
      select count(*)
      from model_hub_queueitemassignment a
      where a.queue_item_id = qi.id and a.deleted = false
    ) as active_assignments,
    (
      select count(*)
      from model_hub_queueitemreviewthread t
      where t.queue_item_id = qi.id and t.deleted = false
    ) as active_review_threads,
    (
      select count(*)
      from model_hub_queueitemreviewcomment c
      where c.queue_item_id = qi.id and c.deleted = false
    ) as active_review_comments
  from model_hub_queueitem qi
  where qi.id = ${sqlUuid(itemId, "itemId")}
)
select coalesce(
  (
    select json_build_object(
      'id', item.id,
      'queue_id', item.queue_id,
      'source_type', item.source_type,
      'status', item.status,
      'review_status', item.review_status,
      'review_notes', item.review_notes,
      'priority', item.priority,
      'order', item."order",
      'metadata', item.metadata,
      'assigned_to_id', item.assigned_to_id,
      'assignment_user_ids',
        coalesce(
          (
            select json_agg(a.user_id::text order by a.user_id::text)
            from model_hub_queueitemassignment a
            where a.queue_item_id = item.id::uuid and a.deleted = false
          ),
          '[]'::json
        ),
      'deleted', item.deleted,
      'deleted_at', item.deleted_at,
      'organization_id', item.organization_id,
      'workspace_id', item.workspace_id,
      'dataset_row_id', item.dataset_row_id,
      'trace_id', item.trace_id,
      'observation_span_id', item.observation_span_id,
      'prototype_run_id', item.prototype_run_id,
      'call_execution_id', item.call_execution_id,
      'trace_session_id', item.trace_session_id,
      'source_fk_count', item.source_fk_count,
      'active_scores', item.active_scores,
      'active_notes', item.active_notes,
      'active_assignments', item.active_assignments,
      'active_review_threads', item.active_review_threads,
      'active_review_comments', item.active_review_comments,
      'active_child_rows',
        item.active_scores +
        item.active_notes +
        item.active_assignments +
        item.active_review_threads +
        item.active_review_comments
    )
    from item
  ),
  '{}'::json
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.id, `Queue item ${itemId} was not found in DB audit.`);
  return audit;
}

function assertQueueItemDbState(dbAudit, expected) {
  const expectedSourceId =
    expected.sourceId ??
    expected.traceId ??
    expected.datasetRowId ??
    expected.observationSpanId ??
    expected.traceSessionId;
  const actualSourceIdByType = {
    dataset_row: dbAudit.dataset_row_id,
    trace: dbAudit.trace_id,
    observation_span: dbAudit.observation_span_id,
    prototype_run: dbAudit.prototype_run_id,
    call_execution: dbAudit.call_execution_id,
    trace_session: dbAudit.trace_session_id,
  };
  assert(
    String(dbAudit.queue_id) === String(expected.queueId) &&
      String(dbAudit.organization_id) === String(expected.organizationId) &&
      String(dbAudit.workspace_id) === String(expected.workspaceId),
    `Queue item DB scope mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.source_type === expected.sourceType &&
      String(actualSourceIdByType[expected.sourceType]) ===
        String(expectedSourceId) &&
      Number(dbAudit.source_fk_count) === 1,
    `Queue item DB source mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.status === expected.status &&
      Number(dbAudit.priority) === Number(expected.priority) &&
      Number(dbAudit.order) === Number(expected.order) &&
      dbAudit.metadata?.stage === expected.metadataStage,
    `Queue item DB state mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.deleted === expected.deleted,
    `Queue item DB deleted flag mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  if (expected.deleted) {
    assert(
      Boolean(dbAudit.deleted_at) && Number(dbAudit.active_child_rows) === 0,
      `Queue item cleanup mismatch: ${JSON.stringify(dbAudit)}.`,
    );
  }
}

function assertAutomationRuleDbState(dbAudit, expected) {
  assert(
    dbAudit.name === expected.name,
    `Automation rule DB name mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    String(dbAudit.queue_id) === String(expected.queueId) &&
      String(dbAudit.organization_id) === String(expected.organizationId) &&
      String(dbAudit.queue_workspace_id) === String(expected.workspaceId),
    `Automation rule DB scope mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.source_type === expected.sourceType &&
      dbAudit.enabled === expected.enabled &&
      dbAudit.trigger_frequency === expected.triggerFrequency,
    `Automation rule DB state mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.conditions?.filter?.[0]?.column_id === expected.firstFilterColumn,
    `Automation rule DB filter mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.deleted === expected.deleted,
    `Automation rule DB deleted flag mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  if (expected.deleted) {
    assert(
      Boolean(dbAudit.deleted_at),
      `Automation rule DB missing deleted_at: ${JSON.stringify(dbAudit)}.`,
    );
  }
}

function buildQueuePutPayload(queue) {
  const labels = asArray(
    queue.labels || queue.queue_labels || queue.annotation_labels,
  );
  const annotators = asArray(queue.annotators || queue.queue_annotators);
  const annotatorIds = [];
  const annotatorRoles = {};

  for (const annotator of annotators) {
    const userId = annotator?.user_id || annotator?.user?.id;
    if (!userId) continue;
    annotatorIds.push(userId);
    const roles = asArray(annotator.roles);
    annotatorRoles[String(userId)] = roles.length
      ? roles
      : [annotator.role || "annotator"];
  }

  return {
    name: queue.name,
    description: queue.description ?? null,
    instructions: queue.instructions ?? null,
    assignment_strategy: queue.assignment_strategy || "manual",
    annotations_required: Number(queue.annotations_required || 1),
    reservation_timeout_minutes: Number(
      queue.reservation_timeout_minutes || 60,
    ),
    requires_review: Boolean(queue.requires_review),
    auto_assign: Boolean(queue.auto_assign),
    label_ids: labels.map(labelId).filter(Boolean),
    annotator_ids: annotatorIds,
    annotator_roles: annotatorRoles,
  };
}

async function putQueueSettings(client, queueId, payload) {
  await client.put(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
    payload,
  );
}

function assertQueueSettingsDbState(dbAudit, expected) {
  assert(
    String(dbAudit.organization_id) === String(expected.organizationId) &&
      String(dbAudit.workspace_id) === String(expected.workspaceId),
    `Queue settings DB scope mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    dbAudit.description === expected.description &&
      Number(dbAudit.reservation_timeout_minutes) ===
        Number(expected.reservationTimeout),
    `Queue settings DB values mismatch: ${JSON.stringify(dbAudit)}.`,
  );
  assert(
    Number(dbAudit.active_label_count) === Number(expected.activeLabelCount) &&
      Number(dbAudit.active_annotator_count) ===
        Number(expected.activeAnnotatorCount),
    `Queue PUT changed active label/member counts: ${JSON.stringify(dbAudit)}.`,
  );
}

async function restoreQueueStatusIfNeeded(client, queueId, expectedStatus) {
  const detail = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
  );
  if (detail?.status === expectedStatus) return;
  await client.post(
    apiPath("/model-hub/annotation-queues/{id}/update-status/", {
      id: queueId,
    }),
    { status: expectedStatus },
  );
}

async function insertAnnotationQueueCreateFixtureDb({
  queueName,
  organizationId,
  workspaceId,
  userId,
}) {
  const queueId = randomUUID();
  const memberId = randomUUID();
  const roles = ["manager", "reviewer", "annotator"];
  const sql = `
WITH inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    created_by_id,
    organization_id,
    workspace_id,
    project_id,
    is_default,
    dataset_id,
    agent_definition_id,
    auto_assign
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(queueId, "queueId")},
    ${sqlString(queueName)},
    ${sqlString("Disposable queue for API journey create coverage.")},
    ${sqlString("Verify labels and creator roles.")},
    'active',
    'manual',
    1,
    45,
    false,
    ${sqlUuid(userId, "userId")},
    ${sqlUuid(organizationId, "organizationId")},
    ${sqlUuid(workspaceId, "workspaceId")},
    NULL,
    false,
    NULL,
    NULL,
    false
  )
  RETURNING id::text, name, status
),
inserted_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    roles,
    queue_id,
    user_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(memberId, "memberId")},
    'manager',
    ${sqlJson(roles)},
    ${sqlUuid(queueId, "queueId")},
    ${sqlUuid(userId, "userId")}
  )
  RETURNING id::text, role, roles
)
SELECT json_build_object(
  'id', (SELECT id FROM inserted_queue),
  'name', (SELECT name FROM inserted_queue),
  'status', (SELECT status FROM inserted_queue),
  'seeded_member_id', (SELECT id FROM inserted_member)
)::text;
`;
  return runPostgresJson(sql);
}

async function insertBulkReviewQueueFixtureDb({
  queueName,
  labelId,
  reviewerId,
  annotatorId,
}) {
  const queueId = randomUUID();
  const queueLabelId = randomUUID();
  const reviewerMemberId = randomUUID();
  const annotatorMemberId = randomUUID();
  const reviewerRoles = ["manager", "reviewer", "annotator"];
  const annotatorRoles = ["annotator"];
  const sql = `
WITH label AS (
  SELECT id, organization_id, workspace_id
  FROM model_hub_annotationslabels
  WHERE id = ${sqlUuid(labelId, "labelId")} AND deleted = false
),
inserted_queue AS (
  INSERT INTO model_hub_annotationqueue (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    created_by_id,
    organization_id,
    workspace_id,
    project_id,
    is_default,
    dataset_id,
    agent_definition_id,
    auto_assign
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(queueId, "queueId")},
    ${sqlString(queueName)},
    ${sqlString("Disposable review queue for API journey bulk-review coverage.")},
    ${sqlString("Verify bulk review and discussion comment edit/delete.")},
    'active',
    'manual',
    1,
    30,
    true,
    ${sqlUuid(reviewerId, "reviewerId")},
    label.organization_id,
    label.workspace_id,
    NULL,
    false,
    NULL,
    NULL,
    false
  FROM label
  RETURNING id::text, name, organization_id::text, workspace_id::text, status, requires_review
),
inserted_queue_label AS (
  INSERT INTO model_hub_annotationqueuelabel (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    required,
    "order",
    label_id,
    queue_id
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(queueLabelId, "queueLabelId")},
    true,
    0,
    ${sqlUuid(labelId, "labelId")},
    ${sqlUuid(queueId, "queueId")}
  FROM inserted_queue
  RETURNING id::text
),
inserted_reviewer_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    roles,
    queue_id,
    user_id
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(reviewerMemberId, "reviewerMemberId")},
    'manager',
    ${sqlJson(reviewerRoles)},
    ${sqlUuid(queueId, "queueId")},
    ${sqlUuid(reviewerId, "reviewerId")}
  FROM inserted_queue
  RETURNING id::text
),
inserted_annotator_member AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    roles,
    queue_id,
    user_id
  )
  SELECT
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(annotatorMemberId, "annotatorMemberId")},
    'annotator',
    ${sqlJson(annotatorRoles)},
    ${sqlUuid(queueId, "queueId")},
    ${sqlUuid(annotatorId, "annotatorId")}
  FROM inserted_queue
  RETURNING id::text
)
SELECT coalesce(
  (
    SELECT json_build_object(
      'id', inserted_queue.id,
      'name', inserted_queue.name,
      'status', inserted_queue.status,
      'requires_review', inserted_queue.requires_review,
      'organization_id', inserted_queue.organization_id,
      'workspace_id', inserted_queue.workspace_id,
      'queue_label_id', (SELECT id FROM inserted_queue_label),
      'reviewer_member_id', (SELECT id FROM inserted_reviewer_member),
      'annotator_member_id', (SELECT id FROM inserted_annotator_member)
    )
    FROM inserted_queue
  ),
  '{}'::json
)::text;
`;
  const queue = await runPostgresJson(sql);
  assert(
    queue?.id,
    `Bulk-review DB seed did not create queue for label ${labelId}.`,
  );
  return queue;
}

async function insertOtherWorkspaceQueueLabelFixtureDb({
  namePrefix,
  organizationId,
  userId,
}) {
  const workspaceId = randomUUID();
  const labelId = randomUUID();
  const workspaceName = `${namePrefix} other label workspace`;
  const labelName = `${namePrefix} other workspace label`;
  const sql = `
WITH inserted_workspace AS (
  INSERT INTO accounts_workspace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    display_name,
    description,
    is_active,
    is_default,
    created_by_id,
    organization_id
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(workspaceId, "workspaceId")},
    ${sqlString(workspaceName)},
    ${sqlString(workspaceName)},
    ${sqlString("Temporary workspace for annotation queue create coverage.")},
    true,
    false,
    ${sqlUuid(userId, "userId")},
    ${sqlUuid(organizationId, "organizationId")}
  )
  RETURNING id::text, name
),
inserted_label AS (
  INSERT INTO model_hub_annotationslabels (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    type,
    settings,
    organization_id,
    description,
    project_id,
    workspace_id,
    metadata,
    allow_notes
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(labelId, "labelId")},
    ${sqlString(labelName)},
    'text',
    '{}'::jsonb,
    ${sqlUuid(organizationId, "organizationId")},
    ${sqlString("Other-workspace label for queue create validation.")},
    NULL,
    ${sqlUuid(workspaceId, "workspaceId")},
    '{}'::jsonb,
    false
  )
  RETURNING id::text, name, workspace_id::text
)
SELECT json_build_object(
  'workspace_id', (SELECT id FROM inserted_workspace),
  'workspace_name', (SELECT name FROM inserted_workspace),
  'label_id', (SELECT id FROM inserted_label),
  'label_name', (SELECT name FROM inserted_label),
  'label_workspace_id', (SELECT workspace_id FROM inserted_label)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadQueueCreateDbAudit({ queueId, userId, labelId }) {
  const sql = `
WITH queue AS (
  SELECT
    q.id::text AS id,
    q.name,
    q.description,
    q.instructions,
    q.status,
    q.organization_id::text AS organization_id,
    q.workspace_id::text AS workspace_id,
    q.created_by_id::text AS created_by_id,
    q.deleted,
    (
      SELECT count(*)::int
      FROM model_hub_annotationqueuelabel ql
      WHERE ql.queue_id = q.id AND ql.deleted = false
    ) AS active_label_count,
    (
      SELECT count(*)::int
      FROM model_hub_annotationqueueannotator qa
      WHERE qa.queue_id = q.id AND qa.deleted = false
    ) AS active_member_count
  FROM model_hub_annotationqueue q
  WHERE q.id = ${sqlUuid(queueId, "queueId")}
),
creator_member AS (
  SELECT id::text, role, roles, deleted
  FROM model_hub_annotationqueueannotator
  WHERE
    queue_id = ${sqlUuid(queueId, "queueId")}
    AND user_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
  ORDER BY updated_at DESC
  LIMIT 1
),
label_binding AS (
  SELECT count(*)::int AS binding_count
  FROM model_hub_annotationqueuelabel
  WHERE
    queue_id = ${sqlUuid(queueId, "queueId")}
    AND label_id = ${sqlUuid(labelId, "labelId")}
    AND deleted = false
)
SELECT coalesce(
  (
    SELECT json_build_object(
      'id', queue.id,
      'name', queue.name,
      'description', queue.description,
      'instructions', queue.instructions,
      'status', queue.status,
      'organization_id', queue.organization_id,
      'workspace_id', queue.workspace_id,
      'created_by_id', queue.created_by_id,
      'deleted', queue.deleted,
      'active_label_count', queue.active_label_count,
      'active_member_count', queue.active_member_count,
      'label_binding_count', (SELECT binding_count FROM label_binding),
      'creator_member', coalesce((SELECT row_to_json(creator_member) FROM creator_member), '{}'::json)
    )
    FROM queue
  ),
  '{}'::json
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(audit?.id, `Queue ${queueId} was not found in create DB audit.`);
  return audit;
}

async function insertTraceSpanSampleFixtureDb({
  namePrefix,
  organizationId,
  workspaceId,
  userId,
}) {
  const projectId = randomUUID();
  const traceId = randomUUID();
  const spanIds = [randomUUID(), randomUUID()];
  const sql = `
WITH inserted_project AS (
  INSERT INTO tracer_project (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    organization_id,
    workspace_id,
    model_type,
    name,
    trace_type,
    metadata,
    config,
    session_config,
    user_id,
    source,
    tags
  )
  VALUES (
    now(),
    now(),
    false,
    NULL,
    ${sqlUuid(projectId, "projectId")},
    ${sqlUuid(organizationId, "organizationId")},
    ${sqlUuid(workspaceId, "workspaceId")},
    'GenerativeLLM',
    ${sqlString(`${namePrefix} trace project`)},
    'observe',
    ${sqlJson({ source: "api-journey", fixture: "annotation-queue-sample" })},
    '[]'::jsonb,
    '[]'::jsonb,
    ${sqlUuid(userId, "userId")},
    'prototype',
    '[]'::jsonb
  )
  RETURNING id
),
inserted_trace AS (
  INSERT INTO tracer_trace (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    project_version_id,
    name,
    metadata,
    input,
    output,
    error,
    session_id,
    external_id,
    tags,
    error_analysis_status
  )
  SELECT
    now() - interval '1 minute',
    now(),
    false,
    NULL,
    ${sqlUuid(traceId, "traceId")},
    p.id,
    NULL,
    ${sqlString(`${namePrefix} trace`)},
    ${sqlJson({ source: "api-journey", fixture: "annotation-queue-sample" })},
    ${sqlJson({ prompt: "seeded annotation queue trace sample" })},
    ${sqlJson({ response: "seeded annotation queue trace response" })},
    NULL,
    NULL,
    ${sqlString(`${namePrefix} trace external`)},
    '[]'::jsonb,
    'completed'
  FROM inserted_project p
  RETURNING id, project_id
),
inserted_span AS (
  INSERT INTO tracer_observation_span (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    project_id,
    project_version_id,
    trace_id,
    parent_span_id,
    name,
    observation_type,
    operation_name,
    start_time,
    end_time,
    input,
    output,
    model,
    model_parameters,
    latency_ms,
    org_id,
    org_user_id,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    response_time,
    eval_id,
    cost,
    status,
    status_message,
    tags,
    metadata,
    span_events,
    provider,
    input_images,
    eval_input,
    eval_attributes,
    custom_eval_config_id,
    eval_status,
    end_user_id,
    prompt_version_id,
    prompt_label_id,
    span_attributes,
    resource_attributes,
    semconv_source
  )
  SELECT
    now() - interval '1 minute',
    now(),
    false,
    NULL::timestamptz,
    ${sqlString(spanIds[0])},
    t.project_id,
    NULL::uuid,
    t.id,
    NULL::text,
    ${sqlString(`${namePrefix} span`)},
    'llm',
    'chat',
    now() - interval '1 minute',
    now(),
    ${sqlJson({ prompt: "seeded annotation queue trace sample" })},
    ${sqlJson({ response: "seeded annotation queue trace response" })},
    'gpt-4o-mini',
    '{}'::jsonb,
    1000,
    ${sqlUuid(organizationId, "organizationId")},
    NULL::uuid,
    4,
    8,
    12,
    1.0,
    NULL::text,
    0,
    'OK',
    NULL::text,
    '[]'::jsonb,
    ${sqlJson({ source: "api-journey", fixture: "annotation-queue-sample" })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
    '[]'::jsonb,
    '{}'::jsonb,
    NULL::uuid,
    'inactive',
    NULL::uuid,
    NULL::uuid,
    NULL::uuid,
    '{}'::jsonb,
    '{}'::jsonb,
    'traceai'
  FROM inserted_trace t
  UNION ALL
  SELECT
    now() - interval '55 seconds',
    now(),
    false,
    NULL::timestamptz,
    ${sqlString(spanIds[1])},
    t.project_id,
    NULL::uuid,
    t.id,
    NULL::text,
    ${sqlString(`${namePrefix} second span`)},
    'llm',
    'chat',
    now() - interval '55 seconds',
    now(),
    ${sqlJson({ prompt: "seeded annotation queue second trace sample" })},
    ${sqlJson({ response: "seeded annotation queue second trace response" })},
    'gpt-4o-mini',
    '{}'::jsonb,
    1000,
    ${sqlUuid(organizationId, "organizationId")},
    NULL::uuid,
    4,
    8,
    12,
    1.0,
    NULL::text,
    0,
    'OK',
    NULL::text,
    '[]'::jsonb,
    ${sqlJson({ source: "api-journey", fixture: "annotation-queue-sample" })},
    '[]'::jsonb,
    'futureagi',
    '[]'::jsonb,
    '[]'::jsonb,
    '{}'::jsonb,
    NULL::uuid,
    'inactive',
    NULL::uuid,
    NULL::uuid,
    NULL::uuid,
    '{}'::jsonb,
    '{}'::jsonb,
    'traceai'
  FROM inserted_trace t
  RETURNING id::text, trace_id::text, project_id::text
)
SELECT json_build_object(
  'project_id', ${sqlString(projectId)},
  'trace_id', ${sqlString(traceId)},
  'span_id', ${sqlString(spanIds[0])},
  'span_ids', ${sqlJson(spanIds)},
  'inserted_project_count', (SELECT count(*) FROM inserted_project),
  'inserted_trace_count', (SELECT count(*) FROM inserted_trace),
  'inserted_span_count', (SELECT count(*) FROM inserted_span)
)::text;
`;
  const sample = await runPostgresJson(sql);
  assert(
    Number(sample.inserted_project_count) === 1 &&
      Number(sample.inserted_trace_count) === 1 &&
      Number(sample.inserted_span_count) === spanIds.length,
    `Trace sample DB seed failed: ${JSON.stringify(sample)}.`,
  );
  return sample;
}

async function deleteTraceSpanFixtureDb(namePrefix) {
  const sql = `
WITH matching_projects AS (
  SELECT id
  FROM tracer_project
  WHERE name LIKE ${sqlString(`${namePrefix}%`)}
),
matching_traces AS (
  SELECT id
  FROM tracer_trace
  WHERE project_id IN (SELECT id FROM matching_projects)
     OR name LIKE ${sqlString(`${namePrefix}%`)}
     OR external_id LIKE ${sqlString(`${namePrefix}%`)}
),
deleted_spans AS (
  DELETE FROM tracer_observation_span
  WHERE project_id IN (SELECT id FROM matching_projects)
     OR trace_id IN (SELECT id FROM matching_traces)
     OR name LIKE ${sqlString(`${namePrefix}%`)}
  RETURNING id
),
deleted_traces AS (
  DELETE FROM tracer_trace
  WHERE id IN (SELECT id FROM matching_traces)
  RETURNING id
),
deleted_projects AS (
  DELETE FROM tracer_project
  WHERE id IN (SELECT id FROM matching_projects)
  RETURNING id
)
SELECT json_build_object(
  'deleted_spans', (SELECT count(*) FROM deleted_spans),
  'deleted_traces', (SELECT count(*) FROM deleted_traces),
  'deleted_projects', (SELECT count(*) FROM deleted_projects)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadQueueListDuplicateDbAudit({
  organizationId,
  workspaceId,
  namePrefix,
}) {
  const sql = `
WITH matching_queues AS (
  SELECT
    id::text,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    auto_assign,
    organization_id::text,
    workspace_id::text,
    created_by_id::text,
    deleted
  FROM model_hub_annotationqueue
  WHERE
    organization_id = ${sqlUuid(organizationId, "organizationId")}
    AND (
      name LIKE ${sqlString(`${namePrefix}%`)}
      OR name LIKE ${sqlString(`Copy of ${namePrefix}%`)}
    )
)
SELECT json_build_object(
  'matching_total_count',
    (SELECT count(*)::int FROM matching_queues),
  'matching_active_count',
    (SELECT count(*)::int FROM matching_queues WHERE deleted = false),
  'matching_deleted_count',
    (SELECT count(*)::int FROM matching_queues WHERE deleted = true),
  'wrong_scope_count',
    (
      SELECT count(*)::int
      FROM matching_queues
      WHERE
        workspace_id IS NULL
        OR workspace_id <> ${sqlUuid(workspaceId, "workspaceId")}::text
    ),
  'rows',
    coalesce(
      (
        SELECT json_agg(row_to_json(matching_queues) ORDER BY name)
        FROM matching_queues
      ),
      '[]'::json
    )
)::text;
`;
  return runPostgresJson(sql);
}

async function loadQueueLimitDbAudit({
  organizationId,
  workspaceId,
  namePrefix,
}) {
  const sql = `
WITH org_queues AS (
  SELECT
    id::text,
    name,
    workspace_id::text,
    deleted
  FROM model_hub_annotationqueue
  WHERE organization_id = ${sqlUuid(organizationId, "organizationId")}
),
matching AS (
  SELECT *
  FROM org_queues
  WHERE name LIKE ${sqlString(`${namePrefix}%`)}
)
SELECT json_build_object(
  'org_active_count',
    (SELECT count(*)::int FROM org_queues WHERE deleted = false),
  'workspace_active_count',
    (
      SELECT count(*)::int
      FROM org_queues
      WHERE
        deleted = false
        AND workspace_id = ${sqlUuid(workspaceId, "workspaceId")}::text
    ),
  'other_workspace_active_count',
    (
      SELECT count(*)::int
      FROM org_queues
      WHERE
        deleted = false
        AND (
          workspace_id IS NULL
          OR workspace_id <> ${sqlUuid(workspaceId, "workspaceId")}::text
        )
    ),
  'matching_active_count',
    (SELECT count(*)::int FROM matching WHERE deleted = false),
  'matching_total_count',
    (SELECT count(*)::int FROM matching),
  'matching_active_member_count',
    (
      SELECT count(*)::int
      FROM model_hub_annotationqueueannotator member
      WHERE
        member.deleted = false
        AND member.queue_id IN (SELECT id::uuid FROM matching WHERE deleted = false)
    ),
  'matching_total_member_count',
    (
      SELECT count(*)::int
      FROM model_hub_annotationqueueannotator member
      WHERE member.queue_id IN (SELECT id::uuid FROM matching)
    ),
  'matching_names',
    coalesce((SELECT json_agg(name ORDER BY name) FROM matching), '[]'::json)
)::text;
`;
  return runPostgresJson(sql);
}

async function createPlanLimitProbeQueue(client, queueName) {
  return client.post(apiPath("/model-hub/annotation-queues/"), {
    name: queueName,
    description:
      "Disposable queue for plan-limit create/delete/retry coverage.",
    instructions: "Verify queue capacity recovers after hard delete.",
    annotations_required: 1,
    reservation_timeout_minutes: 30,
    requires_review: false,
    auto_assign: false,
  });
}

function assertQueueCountDelta(before, current, expectedDelta, label) {
  const expectedOrgCount = Number(before.org_active_count) + expectedDelta;
  const expectedWorkspaceCount =
    Number(before.workspace_active_count) + expectedDelta;
  assert(
    Number(current.org_active_count) === expectedOrgCount &&
      Number(current.workspace_active_count) === expectedWorkspaceCount &&
      Number(current.matching_active_count) === expectedDelta &&
      Number(current.matching_total_count) === expectedDelta,
    `Queue limit DB audit mismatch after ${label}: before=${JSON.stringify(
      before,
    )}, current=${JSON.stringify(current)}, expected_delta=${expectedDelta}.`,
  );
}

function assertQueueDetailRoles(detail, expectedRolesByUserId, label) {
  const annotators = asArray(detail?.annotators);
  for (const [userId, expectedRoles] of Object.entries(expectedRolesByUserId)) {
    const member = annotators.find(
      (annotator) => String(annotator.user_id) === String(userId),
    );
    assert(
      sameJsonValue(asArray(member?.roles), expectedRoles),
      `${label} exposed wrong roles for ${userId}: ${JSON.stringify({
        expectedRoles,
        member,
        detail,
      })}.`,
    );
  }
}

async function loadAnnotationHistoryDbAudit({
  queueId,
  itemId,
  labelId,
  scoreId,
  userId,
}) {
  const sql = `
WITH queue AS (
  SELECT
    id::text,
    organization_id::text,
    workspace_id::text,
    status,
    deleted
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
),
item AS (
  SELECT
    id::text,
    queue_id::text,
    organization_id::text,
    workspace_id::text,
    status,
    source_type,
    trace_id::text,
    deleted
  FROM model_hub_queueitem
  WHERE id = ${sqlUuid(itemId, "itemId")}
),
score_rows AS (
  SELECT
    id::text,
    source_type,
    trace_id::text,
    label_id::text,
    annotator_id::text,
    queue_item_id::text,
    organization_id::text,
    workspace_id::text,
    value,
    value_history,
    score_source,
    notes,
    deleted
  FROM model_hub_score
  WHERE
    id = ${sqlUuid(scoreId, "scoreId")}
    AND queue_item_id = ${sqlUuid(itemId, "itemId")}
    AND label_id = ${sqlUuid(labelId, "labelId")}
    AND annotator_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
),
item_note AS (
  SELECT
    id::text,
    queue_item_id::text,
    annotator_id::text,
    organization_id::text,
    workspace_id::text,
    notes,
    deleted
  FROM model_hub_queueitemnote
  WHERE
    queue_item_id = ${sqlUuid(itemId, "itemId")}
    AND annotator_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
  ORDER BY updated_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'queue', coalesce((SELECT row_to_json(queue) FROM queue), '{}'::json),
  'item', coalesce((SELECT row_to_json(item) FROM item), '{}'::json),
  'score', coalesce((SELECT row_to_json(score_rows) FROM score_rows), '{}'::json),
  'item_note', coalesce((SELECT row_to_json(item_note) FROM item_note), '{}'::json),
  'score_count', (SELECT count(*)::int FROM score_rows),
  'active_item_note_count', (SELECT count(*)::int FROM item_note)
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(
    Number(audit?.score_count || 0) > 0,
    `Score ${scoreId} was not found in annotation history DB audit.`,
  );
  return audit;
}

async function loadQueueNavigationDbAudit({
  queueId,
  completedItemId,
  skippedItemId,
  labelId,
  userId,
}) {
  const sql = `
WITH queue AS (
  SELECT
    id::text,
    organization_id::text,
    workspace_id::text,
    status,
    deleted
  FROM model_hub_annotationqueue
  WHERE id = ${sqlUuid(queueId, "queueId")}
),
items AS (
  SELECT
    qi.id::text,
    qi.queue_id::text,
    qi.organization_id::text,
    qi.workspace_id::text,
    qi.status,
    qi.source_type,
    qi.dataset_row_id::text,
    qi.trace_id::text,
    qi.observation_span_id::text,
    qi.prototype_run_id::text,
    qi.call_execution_id::text,
    qi.trace_session_id::text,
    qi.deleted,
    qi.reserved_by_id::text,
    qi.reserved_at,
    qi.reservation_expires_at,
    (
      (case when qi.dataset_row_id is null then 0 else 1 end) +
      (case when qi.trace_id is null then 0 else 1 end) +
      (case when qi.observation_span_id is null then 0 else 1 end) +
      (case when qi.prototype_run_id is null then 0 else 1 end) +
      (case when qi.call_execution_id is null then 0 else 1 end) +
      (case when qi.trace_session_id is null then 0 else 1 end)
    ) AS source_fk_count
  FROM model_hub_queueitem qi
  WHERE qi.id IN (
    ${sqlUuid(completedItemId, "completedItemId")},
    ${sqlUuid(skippedItemId, "skippedItemId")}
  )
),
completed_score AS (
  SELECT
    id::text,
    queue_item_id::text,
    label_id::text,
    annotator_id::text,
    organization_id::text,
    workspace_id::text,
    source_type,
    trace_id::text,
    value,
    notes,
    score_source,
    deleted
  FROM model_hub_score
  WHERE
    queue_item_id = ${sqlUuid(completedItemId, "completedItemId")}
    AND label_id = ${sqlUuid(labelId, "labelId")}
    AND annotator_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
  ORDER BY updated_at DESC
  LIMIT 1
),
completed_item_note AS (
  SELECT
    id::text,
    queue_item_id::text,
    annotator_id::text,
    organization_id::text,
    workspace_id::text,
    notes,
    deleted
  FROM model_hub_queueitemnote
  WHERE
    queue_item_id = ${sqlUuid(completedItemId, "completedItemId")}
    AND annotator_id = ${sqlUuid(userId, "userId")}
    AND deleted = false
  ORDER BY updated_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'queue',
    coalesce((SELECT row_to_json(queue) FROM queue), '{}'::json),
  'completed_item',
    coalesce(
      (
        SELECT row_to_json(items)
        FROM items
        WHERE id = ${sqlUuid(completedItemId, "completedItemId")}::text
      ),
      '{}'::json
    ),
  'skipped_item',
    coalesce(
      (
        SELECT row_to_json(items)
        FROM items
        WHERE id = ${sqlUuid(skippedItemId, "skippedItemId")}::text
      ),
      '{}'::json
    ),
  'completed_score',
    coalesce(
      (SELECT row_to_json(completed_score) FROM completed_score),
      '{}'::json
    ),
  'completed_item_note',
    coalesce(
      (SELECT row_to_json(completed_item_note) FROM completed_item_note),
      '{}'::json
    ),
  'completed_score_count',
    (
      SELECT count(*)::int
      FROM model_hub_score
      WHERE
        queue_item_id = ${sqlUuid(completedItemId, "completedItemId")}
        AND label_id = ${sqlUuid(labelId, "labelId")}
        AND annotator_id = ${sqlUuid(userId, "userId")}
        AND deleted = false
    ),
  'skipped_score_count',
    (
      SELECT count(*)::int
      FROM model_hub_score
      WHERE
        queue_item_id = ${sqlUuid(skippedItemId, "skippedItemId")}
        AND deleted = false
    )
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(
    audit?.completed_item?.id && audit?.skipped_item?.id,
    `Queue navigation DB audit missed item rows: ${JSON.stringify(audit)}.`,
  );
  return audit;
}

async function loadQueueExportRowDbAudit({ queueId, itemId }) {
  const sql = `
WITH active_queue_labels AS (
  SELECT label_id
  FROM model_hub_annotationqueuelabel
  WHERE queue_id = ${sqlUuid(queueId, "queueId")} AND deleted = false
),
item AS (
  SELECT
    qi.id::text,
    qi.queue_id::text,
    qi.organization_id::text,
    qi.workspace_id::text,
    qi.source_type,
    CASE qi.source_type
      WHEN 'dataset_row' THEN qi.dataset_row_id::text
      WHEN 'trace' THEN qi.trace_id::text
      WHEN 'observation_span' THEN qi.observation_span_id::text
      WHEN 'prototype_run' THEN qi.prototype_run_id::text
      WHEN 'call_execution' THEN qi.call_execution_id::text
      WHEN 'trace_session' THEN qi.trace_session_id::text
      ELSE ''
    END AS source_id,
    qi.status,
    qi."order",
    qi.review_status,
    qi.review_notes,
    qi.reviewed_by_id::text,
    qi.reviewed_at,
    qi.deleted
  FROM model_hub_queueitem qi
  WHERE
    qi.id = ${sqlUuid(itemId, "itemId")}
    AND qi.queue_id = ${sqlUuid(queueId, "queueId")}
    AND qi.deleted = false
),
score_rows AS (
  SELECT
    s.id::text,
    s.queue_item_id::text,
    s.label_id::text,
    label.name AS label_name,
    s.value,
    s.notes,
    s.score_source,
    s.annotator_id::text,
    s.created_at,
    s.updated_at,
    s.deleted
  FROM model_hub_score s
  JOIN active_queue_labels ql ON ql.label_id = s.label_id
  LEFT JOIN model_hub_annotationslabels label ON label.id = s.label_id
  WHERE
    s.queue_item_id = ${sqlUuid(itemId, "itemId")}
    AND s.deleted = false
  ORDER BY s.created_at, s.id
),
item_note AS (
  SELECT
    id::text,
    queue_item_id::text,
    annotator_id::text,
    notes,
    deleted
  FROM model_hub_queueitemnote
  WHERE queue_item_id = ${sqlUuid(itemId, "itemId")} AND deleted = false
  ORDER BY updated_at DESC, created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'item', coalesce((SELECT row_to_json(item) FROM item), '{}'::json),
  'first_score',
    coalesce((SELECT row_to_json(score_rows) FROM score_rows LIMIT 1), '{}'::json),
  'active_score_count', (SELECT count(*)::int FROM score_rows),
  'active_score_label_ids',
    coalesce((SELECT json_agg(label_id ORDER BY created_at, id) FROM score_rows), '[]'::json),
  'item_note',
    coalesce((SELECT row_to_json(item_note) FROM item_note), '{}'::json)
)::text;
`;
  const audit = await runPostgresJson(sql);
  assert(
    audit?.item?.id,
    `Queue export DB audit missed item ${itemId}: ${JSON.stringify(audit)}.`,
  );
  return audit;
}

async function deleteQueueCreateFixturesDb(namePrefix) {
  const sql = `
WITH matching_queues AS (
  SELECT id
  FROM model_hub_annotationqueue
  WHERE
    name LIKE ${sqlString(`${namePrefix}%`)}
    OR name LIKE ${sqlString(`Copy of ${namePrefix}%`)}
),
matching_labels AS (
  SELECT id FROM model_hub_annotationslabels WHERE name LIKE ${sqlString(`${namePrefix}%`)}
),
matching_queue_items AS (
  SELECT id FROM model_hub_queueitem WHERE queue_id IN (SELECT id FROM matching_queues)
),
matching_automation_rules AS (
  SELECT id
  FROM model_hub_automationrule
  WHERE name LIKE ${sqlString(`${namePrefix}%`)}
     OR queue_id IN (SELECT id FROM matching_queues)
),
deleted_scores AS (
  DELETE FROM model_hub_score
  WHERE queue_item_id IN (SELECT id FROM matching_queue_items)
     OR label_id IN (SELECT id FROM matching_labels)
  RETURNING id
),
deleted_item_notes AS (
  DELETE FROM model_hub_queueitemnote
  WHERE queue_item_id IN (SELECT id FROM matching_queue_items)
  RETURNING id
),
deleted_queue_item_assignments AS (
  DELETE FROM model_hub_queueitemassignment
  WHERE queue_item_id IN (SELECT id FROM matching_queue_items)
  RETURNING id
),
deleted_queue_items AS (
  DELETE FROM model_hub_queueitem
  WHERE id IN (SELECT id FROM matching_queue_items)
  RETURNING id
),
deleted_queue_labels AS (
  DELETE FROM model_hub_annotationqueuelabel
  WHERE queue_id IN (SELECT id FROM matching_queues)
     OR label_id IN (SELECT id FROM matching_labels)
  RETURNING id
),
deleted_queue_members AS (
  DELETE FROM model_hub_annotationqueueannotator
  WHERE queue_id IN (SELECT id FROM matching_queues)
  RETURNING id
),
deleted_automation_rules AS (
  DELETE FROM model_hub_automationrule
  WHERE id IN (SELECT id FROM matching_automation_rules)
  RETURNING id
),
deleted_queues AS (
  DELETE FROM model_hub_annotationqueue
  WHERE id IN (SELECT id FROM matching_queues)
  RETURNING id
),
deleted_labels AS (
  DELETE FROM model_hub_annotationslabels
  WHERE id IN (SELECT id FROM matching_labels)
  RETURNING id
),
deleted_workspaces AS (
  DELETE FROM accounts_workspace
  WHERE name LIKE ${sqlString(`${namePrefix}% other label workspace%`)}
  RETURNING id
)
SELECT json_build_object(
  'deleted_scores', (SELECT count(*) FROM deleted_scores),
  'deleted_item_notes', (SELECT count(*) FROM deleted_item_notes),
  'deleted_queue_item_assignments', (SELECT count(*) FROM deleted_queue_item_assignments),
  'deleted_queue_items', (SELECT count(*) FROM deleted_queue_items),
  'deleted_queue_labels', (SELECT count(*) FROM deleted_queue_labels),
  'deleted_queue_members', (SELECT count(*) FROM deleted_queue_members),
  'deleted_automation_rules', (SELECT count(*) FROM deleted_automation_rules),
  'deleted_queues', (SELECT count(*) FROM deleted_queues),
  'deleted_labels', (SELECT count(*) FROM deleted_labels),
  'deleted_workspaces', (SELECT count(*) FROM deleted_workspaces)
)::text;
`;
  return runPostgresJson(sql);
}

async function insertLegacyQueueRoleFixturesDb({
  namePrefix,
  organizationId,
  workspaceId,
  creatorId,
  reviewerId,
}) {
  const legacyQueueId = randomUUID();
  const missingCreatorQueueId = randomUUID();
  const creatorMemberId = randomUUID();
  const reviewerMemberId = randomUUID();
  const legacyQueueName = `${namePrefix} legacy member queue`;
  const missingCreatorQueueName = `${namePrefix} missing creator queue`;
  const sql = `
WITH inserted_queues AS (
  INSERT INTO model_hub_annotationqueue (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    name,
    description,
    instructions,
    status,
    assignment_strategy,
    annotations_required,
    reservation_timeout_minutes,
    requires_review,
    created_by_id,
    organization_id,
    workspace_id,
    project_id,
    is_default,
    dataset_id,
    agent_definition_id,
    auto_assign
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(legacyQueueId, "legacyQueueId")},
      ${sqlString(legacyQueueName)},
      ${sqlString("Disposable legacy-role queue for API journey coverage.")},
      ${sqlString("Verify legacy single-role membership backfill.")},
      'active',
      'manual',
      1,
      30,
      false,
      ${sqlUuid(creatorId, "creatorId")},
      ${sqlUuid(organizationId, "organizationId")},
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL,
      false,
      NULL,
      NULL,
      false
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(missingCreatorQueueId, "missingCreatorQueueId")},
      ${sqlString(missingCreatorQueueName)},
      ${sqlString("Disposable missing-creator membership queue.")},
      ${sqlString("Verify creator membership creation during backfill.")},
      'active',
      'manual',
      1,
      30,
      false,
      ${sqlUuid(creatorId, "creatorId")},
      ${sqlUuid(organizationId, "organizationId")},
      ${sqlUuid(workspaceId, "workspaceId")},
      NULL,
      false,
      NULL,
      NULL,
      false
    )
  RETURNING id::text, name
),
inserted_members AS (
  INSERT INTO model_hub_annotationqueueannotator (
    created_at,
    updated_at,
    deleted,
    deleted_at,
    id,
    role,
    roles,
    queue_id,
    user_id
  )
  VALUES
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(creatorMemberId, "creatorMemberId")},
      'manager',
      ${sqlJson([])},
      ${sqlUuid(legacyQueueId, "legacyQueueId")},
      ${sqlUuid(creatorId, "creatorId")}
    ),
    (
      now(),
      now(),
      false,
      NULL,
      ${sqlUuid(reviewerMemberId, "reviewerMemberId")},
      'reviewer',
      ${sqlJson([])},
      ${sqlUuid(legacyQueueId, "legacyQueueId")},
      ${sqlUuid(reviewerId, "reviewerId")}
    )
  RETURNING id::text, queue_id::text, user_id::text, role, roles
)
SELECT json_build_object(
  'legacy_queue_id', ${sqlString(legacyQueueId)},
  'missing_creator_queue_id', ${sqlString(missingCreatorQueueId)},
  'legacy_queue_name', ${sqlString(legacyQueueName)},
  'missing_creator_queue_name', ${sqlString(missingCreatorQueueName)},
  'inserted_queue_count', (SELECT count(*) FROM inserted_queues),
  'inserted_member_count', (SELECT count(*) FROM inserted_members)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadLegacyRoleBackfillPreflightDb(namePrefix) {
  const sql = `
WITH memberships AS (
  SELECT
    a.id,
    a.role,
    COALESCE(a.roles::jsonb, '[]'::jsonb) AS roles,
    a.user_id,
    q.created_by_id,
    q.name
  FROM model_hub_annotationqueueannotator a
  JOIN model_hub_annotationqueue q ON q.id = a.queue_id
  WHERE q.name NOT LIKE ${sqlString(`${namePrefix}%`)}
),
missing_creator_memberships AS (
  SELECT q.id
  FROM model_hub_annotationqueue q
  WHERE
    q.deleted = false
    AND q.created_by_id IS NOT NULL
    AND q.name NOT LIKE ${sqlString(`${namePrefix}%`)}
    AND NOT EXISTS (
      SELECT 1
      FROM model_hub_annotationqueueannotator a
      WHERE
        a.queue_id = q.id
        AND a.user_id = q.created_by_id
        AND a.deleted = false
    )
)
SELECT json_build_object(
  'stale_membership_count',
    (
      SELECT count(*)
      FROM memberships
      WHERE
        roles = '[]'::jsonb
        OR (
          created_by_id = user_id
          AND NOT (
            roles ? 'manager'
            AND roles ? 'reviewer'
            AND roles ? 'annotator'
          )
        )
        OR (
          roles <> '[]'::jsonb
          AND role <> CASE
            WHEN roles ? 'manager' THEN 'manager'
            WHEN roles ? 'reviewer' THEN 'reviewer'
            WHEN roles ? 'annotator' THEN 'annotator'
            ELSE role
          END
        )
    ),
  'missing_creator_membership_count',
    (SELECT count(*) FROM missing_creator_memberships)
)::text;
`;
  return runPostgresJson(sql);
}

async function loadLegacyRoleFixtureAuditDb({
  namePrefix,
  creatorId,
  reviewerId,
}) {
  const sql = `
WITH matching_queues AS (
  SELECT
    q.id,
    q.id::text AS id_text,
    q.name,
    q.created_by_id,
    q.created_by_id::text AS created_by_id_text,
    q.organization_id::text AS organization_id,
    q.workspace_id::text AS workspace_id,
    q.deleted
  FROM model_hub_annotationqueue q
  WHERE q.name LIKE ${sqlString(`${namePrefix}%`)}
),
matching_members AS (
  SELECT
    a.id::text AS id,
    a.queue_id::text AS queue_id,
    a.user_id::text AS user_id,
    a.role,
    a.roles,
    a.deleted
  FROM model_hub_annotationqueueannotator a
  WHERE a.queue_id IN (SELECT id FROM matching_queues)
)
SELECT json_build_object(
  'queues',
    COALESCE(
      (
        SELECT json_agg(
          json_build_object(
            'id', id_text,
            'name', name,
            'created_by_id', created_by_id_text,
            'organization_id', organization_id,
            'workspace_id', workspace_id,
            'deleted', deleted
          )
          ORDER BY name
        )
        FROM matching_queues
      ),
      '[]'::json
    ),
  'members',
    COALESCE(
      (
        SELECT json_agg(
          json_build_object(
            'id', id,
            'queue_id', queue_id,
            'user_id', user_id,
            'role', role,
            'roles', roles,
            'deleted', deleted
          )
          ORDER BY queue_id, user_id
        )
        FROM matching_members
      ),
      '[]'::json
    ),
  'creator_member_count',
    (
      SELECT count(*)
      FROM matching_members
      WHERE user_id = ${sqlString(creatorId)} AND deleted = false
    ),
  'reviewer_member_count',
    (
      SELECT count(*)
      FROM matching_members
      WHERE user_id = ${sqlString(reviewerId)} AND deleted = false
    ),
  'missing_creator_membership_count',
    (
      SELECT count(*)
      FROM matching_queues q
      WHERE
        q.created_by_id = ${sqlUuid(creatorId, "creatorId")}
        AND NOT EXISTS (
          SELECT 1
          FROM matching_members m
          WHERE
            m.queue_id = q.id_text
            AND m.user_id = ${sqlString(creatorId)}
            AND m.deleted = false
        )
    )
)::text;
`;
  return runPostgresJson(sql);
}

async function loadLegacyRoleFixtureResidueDb(namePrefix) {
  const sql = `
WITH matching_queues AS (
  SELECT id FROM model_hub_annotationqueue WHERE name LIKE ${sqlString(`${namePrefix}%`)}
)
SELECT json_build_object(
  'matching_queue_count', (SELECT count(*) FROM matching_queues),
  'matching_member_count',
    (
      SELECT count(*)
      FROM model_hub_annotationqueueannotator
      WHERE queue_id IN (SELECT id FROM matching_queues)
    )
)::text;
`;
  return runPostgresJson(sql);
}

function findLegacyRoleAuditMember(audit, queueId, userId) {
  return asArray(audit?.members).find(
    (member) =>
      String(member.queue_id) === String(queueId) &&
      String(member.user_id) === String(userId) &&
      member.deleted === false,
  );
}

async function requireBackendContainerForJourney() {
  const container =
    process.env.API_JOURNEY_BACKEND_CONTAINER || "futureagi-ws2-backend-1";
  try {
    await execFileAsync("docker", ["exec", container, "true"]);
  } catch {
    skip(
      `Set API_JOURNEY_BACKEND_CONTAINER to a running backend container for management-command coverage; ${container} is unavailable.`,
    );
  }
  return container;
}

async function runBackendManageCommand(container, commandName, args = []) {
  const manageArgs = [commandName, ...args].map(shellQuote).join(" ");
  const command = [
    "cd /app/backend",
    `UV_PROJECT_ENVIRONMENT=/tmp/ws2-pytest-venv UV_LINK_MODE=copy uv run python manage.py ${manageArgs}`,
  ].join(" && ");
  try {
    return await execFileAsync(
      "docker",
      ["exec", container, "sh", "-lc", command],
      {
        maxBuffer: 20 * 1024 * 1024,
      },
    );
  } catch (error) {
    throw new Error(
      `Backend manage.py ${commandName} failed: ${String(
        error.stderr || error.stdout || error.message,
      ).slice(0, 2000)}`,
    );
  }
}

async function runBackendShellScript(container, script) {
  const command = [
    "cd /app/backend",
    `UV_PROJECT_ENVIRONMENT=/tmp/ws2-pytest-venv UV_LINK_MODE=copy uv run python manage.py shell -c ${shellQuote(
      script,
    )}`,
  ].join(" && ");
  try {
    return await execFileAsync(
      "docker",
      ["exec", container, "sh", "-lc", command],
      {
        maxBuffer: 20 * 1024 * 1024,
      },
    );
  } catch (error) {
    throw new Error(
      `Backend shell script failed: ${String(
        error.stderr || error.stdout || error.message,
      ).slice(0, 2000)}`,
    );
  }
}

async function runBackendShellJson(container, script) {
  const marker = "API_JOURNEY_JSON=";
  const { stdout } = await runBackendShellScript(container, script);
  const line = String(stdout || "")
    .split(/\r?\n/)
    .find((candidate) => candidate.startsWith(marker));
  assert(line, `Backend shell script returned no ${marker} line: ${stdout}`);
  return JSON.parse(line.slice(marker.length));
}

async function loadBackendQueuePricingMode(backendContainer) {
  const script = `
import json

from tfc.ee_gating import is_oss

mode = "unknown"
try:
    from ee.usage.deployment import DeploymentMode

    mode = DeploymentMode.get_mode()
except Exception as exc:
    mode = f"unknown:{type(exc).__name__}"
print("API_JOURNEY_JSON=" + json.dumps({"is_oss": is_oss(), "mode": mode}))
`;
  return runBackendShellJson(backendContainer, script);
}

async function setTemporaryQueueLimitOverride({
  backendContainer,
  organizationId,
  limit,
}) {
  const script = `
import json

from ee.usage.models.usage import OrganizationSubscription, PlanEntitlement
from ee.usage.services.entitlements import Entitlements
from model_hub.models.annotation_queues import AnnotationQueue

org_id = ${JSON.stringify(organizationId)}
feature = "queues"
limit = ${Number(limit)}
existing_active = list(
    PlanEntitlement.objects.filter(
        organization_id=org_id,
        feature=feature,
    ).values("id", "plan", "value_int", "value_bool")
)
if existing_active:
    raise RuntimeError(f"Active queue entitlement override already exists: {existing_active}")
plan = (
    OrganizationSubscription.objects.filter(organization_id=org_id)
    .values_list("plan", flat=True)
    .first()
    or "free"
)
override, created = PlanEntitlement.all_objects.update_or_create(
    organization_id=org_id,
    feature=feature,
    plan=plan,
    defaults={
        "value_int": limit,
        "value_bool": None,
        "deleted": False,
        "deleted_at": None,
    },
)
Entitlements.invalidate_cache(org_id, feature)
current_count = AnnotationQueue.no_workspace_objects.filter(
    organization_id=org_id,
).count()
print(
    "API_JOURNEY_JSON="
    + json.dumps(
        {
            "override_id": str(override.id),
            "created": created,
            "plan": plan,
            "limit": limit,
            "current_count": current_count,
        }
    )
)
`;
  return runBackendShellJson(backendContainer, script);
}

async function clearTemporaryQueueLimitOverride({
  backendContainer,
  organizationId,
  overrideId,
}) {
  if (!overrideId) return { deleted: 0, skipped: true };
  const script = `
import json

from ee.usage.models.usage import PlanEntitlement
from ee.usage.services.entitlements import Entitlements

org_id = ${JSON.stringify(organizationId)}
override_id = ${JSON.stringify(overrideId)}
feature = "queues"
deleted, _ = PlanEntitlement.all_objects.filter(
    id=override_id,
    organization_id=org_id,
    feature=feature,
).delete()
Entitlements.invalidate_cache(org_id, feature)
print("API_JOURNEY_JSON=" + json.dumps({"deleted": deleted}))
`;
  return runBackendShellJson(backendContainer, script);
}

function parseAnnotationQueueRoleBackfillSummary(output) {
  const match = String(output || "").match(
    /(?:DRY RUN:\s*)?Annotation queue role backfill complete:\s*(\d+)\s+memberships updated,\s*(\d+)\s+creator memberships created/i,
  );
  assert(
    match,
    `Could not parse annotation queue role backfill summary: ${output}`,
  );
  return {
    dryRun: /DRY RUN:/i.test(output),
    updated: Number(match[1]),
    created: Number(match[2]),
  };
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", "'\"'\"'")}'`;
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

function sqlUuid(value, label) {
  assert(isUuid(value), `${label} must be a UUID for DB audit SQL.`);
  return `'${value}'::uuid`;
}

function sqlString(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function sqlJson(value) {
  return `${sqlString(JSON.stringify(value ?? null))}::jsonb`;
}

function expectedProgressFromDb(dbAudit, userId) {
  const items = asArray(dbAudit.items);
  const scores = asArray(dbAudit.scores);
  const assignments = asArray(dbAudit.assignments);
  const total = items.length;
  const statusCounts = countBy(items, (item) => item.status);
  const inReview = items.filter(
    (item) => item.review_status === "pending_review",
  ).length;
  const inProgress = Math.max(
    Number(statusCounts.get("in_progress") || 0) - inReview,
    0,
  );
  const completed = Number(statusCounts.get("completed") || 0);

  const assignedStats = new Map();
  for (const item of items.filter((row) => row.assigned_to)) {
    const key = String(item.assigned_to);
    if (!assignedStats.has(key)) {
      assignedStats.set(key, {
        user_id: key,
        name: item.assigned_to_name || null,
        completed: 0,
        pending: 0,
        in_progress: 0,
        in_review: 0,
        annotations_count: 0,
      });
    }
    const stats = assignedStats.get(key);
    if (item.status === "completed") stats.completed += 1;
    if (item.status === "pending") stats.pending += 1;
    if (
      item.status === "in_progress" &&
      item.review_status !== "pending_review"
    ) {
      stats.in_progress += 1;
    }
    if (item.review_status === "pending_review") stats.in_review += 1;
  }

  for (const [annotatorId, rows] of groupBy(
    scores.filter((score) => score.annotator_id),
    (score) => String(score.annotator_id),
  )) {
    if (!assignedStats.has(annotatorId)) {
      assignedStats.set(annotatorId, {
        user_id: annotatorId,
        name: rows[0]?.annotator_name || null,
        completed: 0,
        pending: 0,
        in_progress: 0,
        in_review: 0,
        annotations_count: rows.length,
      });
    } else {
      assignedStats.get(annotatorId).annotations_count = rows.length;
    }
  }

  const assignedItemIds = new Set(
    assignments
      .filter((assignment) => String(assignment.user_id) === String(userId))
      .map((assignment) => String(assignment.queue_item_id)),
  );
  const userItems = items.filter(
    (item) =>
      String(item.assigned_to || "") === String(userId) ||
      assignedItemIds.has(String(item.id)),
  );
  const userStatusCounts = countBy(userItems, (item) => item.status);
  const userInReview = userItems.filter(
    (item) => item.review_status === "pending_review",
  ).length;
  const userInProgress = Math.max(
    Number(userStatusCounts.get("in_progress") || 0) - userInReview,
    0,
  );
  const userCompleted = Number(userStatusCounts.get("completed") || 0);

  return {
    total,
    pending: Number(statusCounts.get("pending") || 0),
    in_progress: inProgress,
    in_review: inReview,
    completed,
    skipped: Number(statusCounts.get("skipped") || 0),
    progress_pct: total > 0 ? round1((completed / total) * 100) : 0,
    annotator_stats: Array.from(assignedStats.values()),
    user_progress: {
      total: userItems.length,
      completed: userCompleted,
      pending: Number(userStatusCounts.get("pending") || 0),
      in_progress: userInProgress,
      in_review: userInReview,
      skipped: Number(userStatusCounts.get("skipped") || 0),
      progress_pct:
        userItems.length > 0
          ? round1((userCompleted / userItems.length) * 100)
          : 0,
    },
  };
}

function expectedAnalyticsFromDb(dbAudit) {
  const items = asArray(dbAudit.items);
  const scores = asArray(dbAudit.scores);
  const addressedReviewItemIds = new Set(
    asArray(dbAudit.addressed_review_item_ids).map((id) => String(id)),
  );
  const statusBreakdown = {
    pending: 0,
    in_review: 0,
    needs_changes: 0,
    resubmitted: 0,
    completed: 0,
    skipped: 0,
  };
  for (const item of items) {
    if (item.review_status === "pending_review") {
      if (addressedReviewItemIds.has(String(item.id))) {
        statusBreakdown.resubmitted += 1;
      } else {
        statusBreakdown.in_review += 1;
      }
    } else if (item.review_status === "rejected") {
      statusBreakdown.needs_changes += 1;
    } else if (item.status === "in_progress") {
      statusBreakdown.in_review += 1;
    } else if (
      Object.prototype.hasOwnProperty.call(statusBreakdown, item.status)
    ) {
      statusBreakdown[item.status] += 1;
    } else {
      statusBreakdown.pending += 1;
    }
  }

  const now = Date.now();
  const thirtyDaysAgo = now - 30 * 24 * 60 * 60 * 1000;
  const completedInWindow = items.filter(
    (item) =>
      item.status === "completed" &&
      Number.isFinite(Date.parse(item.updated_at)) &&
      Date.parse(item.updated_at) >= thirtyDaysAgo,
  );
  const completedItemIds = new Set(
    items
      .filter((item) => item.status === "completed")
      .map((item) => String(item.id)),
  );
  const dailyCounts = new Map();
  for (const item of completedInWindow) {
    const date = new Date(item.updated_at).toISOString().slice(0, 10);
    dailyCounts.set(date, Number(dailyCounts.get(date) || 0) + 1);
  }

  const annotatorPerformance = Array.from(
    groupBy(
      scores.filter((score) => score.annotator_id),
      (score) => String(score.annotator_id),
    ),
  )
    .map(([annotatorId, rows]) => {
      const completedQueueItemIds = new Set(
        rows
          .filter((row) => completedItemIds.has(String(row.queue_item_id)))
          .map((row) => String(row.queue_item_id)),
      );
      return {
        user_id: annotatorId,
        name: rows[0]?.annotator_name || null,
        completed: completedQueueItemIds.size,
        last_active:
          rows
            .map((row) => row.created_at)
            .filter(Boolean)
            .sort()
            .at(-1) || null,
      };
    })
    .sort(
      (left, right) =>
        right.completed - left.completed ||
        String(left.user_id).localeCompare(String(right.user_id)),
    );

  const labelDistribution = {};
  for (const score of scores) {
    const labelIdValue = String(score.label_id);
    if (!labelDistribution[labelIdValue]) {
      labelDistribution[labelIdValue] = {
        name: score.label_name,
        type: score.label_type,
        values: {},
      };
    }
    const key = pythonValueKey(score.value);
    labelDistribution[labelIdValue].values[key] =
      Number(labelDistribution[labelIdValue].values[key] || 0) + 1;
  }

  return {
    throughput: {
      daily: Array.from(dailyCounts.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([date, count]) => ({ date, count })),
      total_completed: Number(statusBreakdown.completed || 0),
      avg_per_day: round1(completedInWindow.length / 30),
    },
    annotator_performance: annotatorPerformance,
    label_distribution: labelDistribution,
    status_breakdown: statusBreakdown,
    total: Object.values(statusBreakdown).reduce(
      (sum, value) => sum + Number(value || 0),
      0,
    ),
  };
}

function expectedAgreementFromDb(dbAudit) {
  const scores = asArray(dbAudit.scores);
  const itemLabelMap = new Map();
  const labelInfo = new Map();
  for (const score of scores) {
    const key = `${score.queue_item_id}::${score.label_id}`;
    if (!itemLabelMap.has(key)) itemLabelMap.set(key, []);
    itemLabelMap.get(key).push({
      queue_item_id: String(score.queue_item_id),
      label_id: String(score.label_id),
      annotator_id: String(score.annotator_id),
      value: score.value,
    });
    if (!labelInfo.has(String(score.label_id))) {
      labelInfo.set(String(score.label_id), {
        name: score.label_name,
        type: score.label_type,
      });
    }
  }

  const labels = {};
  for (const [labelIdValue, info] of labelInfo) {
    let agreeCount = 0;
    let totalCount = 0;
    const disagreementItems = [];
    for (const entries of itemLabelMap.values()) {
      if (entries[0]?.label_id !== labelIdValue || entries.length < 2) continue;
      totalCount += 1;
      const values = entries.map((entry) =>
        normalizeAgreementValue(entry.value),
      );
      if (new Set(values).size === 1) {
        agreeCount += 1;
      } else {
        disagreementItems.push(entries[0].queue_item_id);
      }
    }
    const agreementPct = totalCount > 0 ? agreeCount / totalCount : null;
    const comparableForKappa = [
      "categorical",
      "numeric",
      "star",
      "thumbs_up_down",
    ].includes(info.type);
    const kappa =
      comparableForKappa && totalCount > 0
        ? cohensKappa(itemLabelMap, labelIdValue)
        : null;
    labels[labelIdValue] = {
      label_name: info.name,
      label_type: info.type,
      agreement_pct: agreementPct === null ? null : round3(agreementPct),
      cohens_kappa: kappa === null ? null : round3(kappa),
      disagreement_count: disagreementItems.length,
      disagreement_items: disagreementItems.slice(0, 20),
    };
  }

  let totalPairs = 0;
  let agreePairs = 0;
  for (const entries of itemLabelMap.values()) {
    if (entries.length < 2) continue;
    totalPairs += 1;
    const values = entries.map((entry) => normalizeAgreementValue(entry.value));
    if (new Set(values).size === 1) agreePairs += 1;
  }

  return {
    overall_agreement: totalPairs > 0 ? round3(agreePairs / totalPairs) : null,
    labels,
    annotator_pairs: annotatorPairAgreement(itemLabelMap),
  };
}

function assertExportFieldsCatalogMatches(payload, dbAudit) {
  const fields = asArray(payload.fields);
  const defaultMapping = asArray(payload.default_mapping);
  assert(fields.length > 0, "Export fields returned no fields.");
  assert(
    defaultMapping.length > 0,
    "Export fields returned no default_mapping.",
  );

  const fieldsById = new Map(fields.map((field) => [String(field.id), field]));
  assert(
    fieldsById.size === fields.length,
    "Export fields contained duplicate field ids.",
  );
  const columnKeys = fields.map((field) =>
    String(field.column || "")
      .trim()
      .toLowerCase(),
  );
  assert(
    new Set(columnKeys).size === columnKeys.length,
    "Export fields contained duplicate column names.",
  );

  const defaultIds = new Set(
    fields.filter((field) => field.default === true).map((field) => field.id),
  );
  const mappedIds = new Set(defaultMapping.map((mapping) => mapping.field));
  assert(
    defaultIds.size === mappedIds.size &&
      [...defaultIds].every((fieldId) => mappedIds.has(fieldId)),
    "Export fields default_mapping did not match fields marked default=true.",
  );
  for (const mapping of defaultMapping) {
    assert(
      fieldsById.has(String(mapping.field)),
      `Default mapping field ${mapping.field} missing.`,
    );
    assert(
      mapping.enabled === true,
      "Default mapping must mark fields enabled.",
    );
    assert(
      typeof mapping.column === "string" && mapping.column.length > 0,
      "Default mapping column must be a non-empty string.",
    );
  }

  const requiredBaseFields = [
    "source_type",
    "source_id",
    "source_name",
    "project_id",
    "item_status",
    "item_order",
    "queue_requires_review",
    "review_status",
    "eval_metrics",
    "annotation_metrics",
    "item_notes",
  ];
  for (const fieldId of requiredBaseFields) {
    assert(
      fieldsById.has(fieldId),
      `Export fields missing base field ${fieldId}.`,
    );
  }

  const annotationsRequired = Math.max(
    Number(dbAudit.queue?.annotations_required || 1),
    1,
  );
  const scoreCountsByLabel = new Map();
  for (const row of asArray(dbAudit.score_counts)) {
    const labelKey = String(row.label_id);
    scoreCountsByLabel.set(
      labelKey,
      Math.max(
        Number(scoreCountsByLabel.get(labelKey) || 0),
        Number(row.score_count || 0),
      ),
    );
  }

  for (const label of asArray(dbAudit.labels)) {
    const labelIdValue = String(label.label_id);
    const expectedDataType = exportFieldDataTypeForLabel(label.type);
    assertFieldShape(fieldsById, `label:${labelIdValue}:value`, {
      kind: "value",
      label_id: labelIdValue,
      group: "Annotations",
      data_type: expectedDataType,
      default: false,
    });
    for (const kind of [
      "notes",
      "annotator_id",
      "annotator_name",
      "annotator_email",
      "score_source",
      "created_at",
      "annotation",
    ]) {
      assertFieldShape(fieldsById, `label:${labelIdValue}:${kind}`, {
        kind,
        label_id: labelIdValue,
        group: "Annotations",
        default: false,
      });
    }

    const slotCount = Math.max(
      annotationsRequired,
      Number(scoreCountsByLabel.get(labelIdValue) || 0),
      1,
    );
    const expectedExpandFields = [];
    for (let slot = 1; slot <= slotCount; slot += 1) {
      for (const kind of annotationSlotFieldKinds()) {
        const fieldId = `label:${labelIdValue}:slot:${slot}:${kind}`;
        expectedExpandFields.push(fieldId);
        assertFieldShape(fieldsById, fieldId, {
          kind,
          label_id: labelIdValue,
          group: "Annotations",
          default: true,
          slot,
        });
      }
    }
    const bundleId = `label:${labelIdValue}:annotation_columns`;
    assertFieldShape(fieldsById, bundleId, {
      kind: "annotation_bundle",
      label_id: labelIdValue,
      group: "Annotations",
      default: false,
    });
    assertJsonEqual(
      asArray(fieldsById.get(bundleId).expand_fields),
      expectedExpandFields,
      `Export fields ${bundleId} expand_fields`,
    );
  }

  const sourceTypes = new Set(
    asArray(dbAudit.sample_items)
      .map((item) => item.source_type)
      .filter(Boolean),
  );
  const attrFields = fields.filter((field) =>
    String(field.id || "").startsWith("attr:"),
  );
  if (sourceTypes.size > 1 && attrFields.length > 0) {
    assert(
      attrFields.every((field) => field.source_type),
      "Multi-source export attribute fields must include source_type.",
    );
  }
}

function assertFieldShape(fieldsById, fieldId, expected) {
  const field = fieldsById.get(fieldId);
  assert(field, `Export fields missing ${fieldId}.`);
  for (const [key, value] of Object.entries(expected)) {
    assert(
      String(field[key]) === String(value),
      `Export field ${fieldId}.${key} expected ${value}, saw ${field[key]}.`,
    );
  }
}

function annotationSlotFieldKinds() {
  return [
    "value",
    "annotator_name",
    "annotator_email",
    "annotator_id",
    "notes",
    "score_source",
    "created_at",
    "updated_at",
    "annotation",
  ];
}

function exportFieldDataTypeForLabel(type) {
  switch (String(type || "")) {
    case "numeric":
    case "star":
      return "float";
    case "categorical":
      return "array";
    case "thumbs_up_down":
    case "text":
    default:
      return "text";
  }
}

function assertProgressMatches(actual, expected) {
  for (const key of [
    "total",
    "pending",
    "in_progress",
    "in_review",
    "completed",
    "skipped",
  ]) {
    assert(
      Number(actual[key]) === Number(expected[key]),
      `Progress ${key} expected ${expected[key]}, saw ${actual[key]}.`,
    );
  }
  assert(
    nearlyEqual(actual.progress_pct, expected.progress_pct),
    `Progress pct expected ${expected.progress_pct}, saw ${actual.progress_pct}.`,
  );
  assertMetricRowsMatch(
    asArray(actual.annotator_stats),
    expected.annotator_stats,
    "user_id",
    ["completed", "pending", "in_progress", "in_review", "annotations_count"],
    "progress annotator_stats",
  );
  assertProgressObjectMatches(
    actual.user_progress || {},
    expected.user_progress,
    "user_progress",
  );
}

function assertProgressObjectMatches(actual, expected, label) {
  for (const key of [
    "total",
    "completed",
    "pending",
    "in_progress",
    "in_review",
    "skipped",
  ]) {
    assert(
      Number(actual[key]) === Number(expected[key]),
      `${label} ${key} expected ${expected[key]}, saw ${actual[key]}.`,
    );
  }
  assert(
    nearlyEqual(actual.progress_pct, expected.progress_pct),
    `${label} progress_pct expected ${expected.progress_pct}, saw ${actual.progress_pct}.`,
  );
}

function assertAnalyticsMatches(actual, expected) {
  assert(
    Number(actual.total) === Number(expected.total),
    `Analytics total expected ${expected.total}, saw ${actual.total}.`,
  );
  for (const [key, value] of Object.entries(expected.status_breakdown)) {
    assert(
      Number(actual.status_breakdown?.[key]) === Number(value),
      `Analytics status ${key} expected ${value}, saw ${actual.status_breakdown?.[key]}.`,
    );
  }
  assert(
    !Object.prototype.hasOwnProperty.call(
      actual.status_breakdown || {},
      "in_progress",
    ),
    "Analytics status_breakdown leaked in_progress bucket.",
  );
  assert(
    Number(actual.throughput?.total_completed) ===
      Number(expected.throughput.total_completed),
    `Analytics total_completed expected ${expected.throughput.total_completed}, saw ${actual.throughput?.total_completed}.`,
  );
  assert(
    nearlyEqual(
      actual.throughput?.avg_per_day,
      expected.throughput.avg_per_day,
    ),
    `Analytics avg_per_day expected ${expected.throughput.avg_per_day}, saw ${actual.throughput?.avg_per_day}.`,
  );
  assertJsonEqual(
    asArray(actual.throughput?.daily),
    expected.throughput.daily,
    "Analytics daily throughput",
  );
  assertMetricRowsMatch(
    asArray(actual.annotator_performance),
    expected.annotator_performance,
    "user_id",
    ["completed"],
    "analytics annotator_performance",
  );
  assertLabelDistributionMatches(
    actual.label_distribution || {},
    expected.label_distribution,
  );
}

function assertAgreementMatches(actual, expected) {
  assert(
    nullableNearlyEqual(actual.overall_agreement, expected.overall_agreement),
    `Agreement overall expected ${expected.overall_agreement}, saw ${actual.overall_agreement}.`,
  );
  for (const [labelIdValue, expectedLabel] of Object.entries(expected.labels)) {
    const actualLabel = actual.labels?.[labelIdValue];
    assert(actualLabel, `Agreement label ${labelIdValue} missing from API.`);
    assert(
      actualLabel.label_name === expectedLabel.label_name &&
        actualLabel.label_type === expectedLabel.label_type,
      `Agreement label ${labelIdValue} name/type mismatch.`,
    );
    assert(
      nullableNearlyEqual(
        actualLabel.agreement_pct,
        expectedLabel.agreement_pct,
      ) &&
        nullableNearlyEqual(
          actualLabel.cohens_kappa,
          expectedLabel.cohens_kappa,
        ),
      `Agreement label ${labelIdValue} metrics expected ${JSON.stringify(
        expectedLabel,
      )}, saw ${JSON.stringify(actualLabel)}.`,
    );
    assert(
      Number(actualLabel.disagreement_count) ===
        Number(expectedLabel.disagreement_count),
      `Agreement label ${labelIdValue} disagreement_count expected ${expectedLabel.disagreement_count}, saw ${actualLabel.disagreement_count}.`,
    );
    assertJsonEqual(
      asArray(actualLabel.disagreement_items),
      expectedLabel.disagreement_items,
      `Agreement label ${labelIdValue} disagreement_items`,
    );
  }
  assertMetricRowsMatch(
    asArray(actual.annotator_pairs),
    expected.annotator_pairs,
    (row) => `${row.annotator_1_id}:${row.annotator_2_id}`,
    ["agreement_pct", "total_comparisons"],
    "agreement annotator_pairs",
  );
}

function assertMetricRowsMatch(
  actualRows,
  expectedRows,
  keySpec,
  numericFields,
  label,
) {
  const keyFn =
    typeof keySpec === "function" ? keySpec : (row) => String(row?.[keySpec]);
  const actualByKey = new Map(actualRows.map((row) => [keyFn(row), row]));
  const expectedByKey = new Map(expectedRows.map((row) => [keyFn(row), row]));
  assert(
    actualByKey.size === expectedByKey.size,
    `${label} row count expected ${expectedByKey.size}, saw ${actualByKey.size}.`,
  );
  for (const [key, expected] of expectedByKey) {
    const actual = actualByKey.get(key);
    assert(actual, `${label} missing row ${key}.`);
    for (const field of numericFields) {
      const matches = field.includes("pct")
        ? nearlyEqual(actual[field], expected[field])
        : Number(actual[field]) === Number(expected[field]);
      assert(
        matches,
        `${label} ${key}.${field} expected ${expected[field]}, saw ${actual[field]}.`,
      );
    }
  }
}

function assertLabelDistributionMatches(actual, expected) {
  const actualKeys = Object.keys(actual).sort();
  const expectedKeys = Object.keys(expected).sort();
  assertJsonEqual(
    actualKeys,
    expectedKeys,
    "Analytics label_distribution labels",
  );
  for (const labelIdValue of expectedKeys) {
    assert(
      actual[labelIdValue]?.name === expected[labelIdValue].name &&
        actual[labelIdValue]?.type === expected[labelIdValue].type,
      `Analytics label_distribution ${labelIdValue} name/type mismatch.`,
    );
    assertJsonEqual(
      actual[labelIdValue]?.values || {},
      expected[labelIdValue].values,
      `Analytics label_distribution ${labelIdValue} values`,
    );
  }
}

function assertJsonEqual(actual, expected, label) {
  const actualJson = JSON.stringify(sortJson(actual));
  const expectedJson = JSON.stringify(sortJson(expected));
  assert(
    actualJson === expectedJson,
    `${label} expected ${expectedJson}, saw ${actualJson}.`,
  );
}

function sortJson(value) {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortJson(value[key])]),
  );
}

function countBy(rows, keyFn) {
  const counts = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    counts.set(key, Number(counts.get(key) || 0) + 1);
  }
  return counts;
}

function groupBy(rows, keyFn) {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return groups;
}

function countComparableItemLabels(scores) {
  return Array.from(
    groupBy(
      asArray(scores),
      (score) => `${score.queue_item_id}::${score.label_id}`,
    ),
  ).filter(([, rows]) => rows.length >= 2).length;
}

function combinations(values) {
  const pairs = [];
  for (let index = 0; index < values.length; index += 1) {
    for (let other = index + 1; other < values.length; other += 1) {
      pairs.push([values[index], values[other]]);
    }
  }
  return pairs;
}

function cohensKappa(itemLabelMap, labelIdValue) {
  const pairs = [];
  const allValues = [];
  for (const entries of itemLabelMap.values()) {
    if (entries[0]?.label_id !== labelIdValue || entries.length < 2) continue;
    for (const [left, right] of combinations(entries)) {
      const leftValue = normalizeAgreementValue(left.value);
      const rightValue = normalizeAgreementValue(right.value);
      pairs.push([leftValue, rightValue]);
      allValues.push(leftValue, rightValue);
    }
  }
  if (!pairs.length) return null;
  const observed =
    pairs.filter(([left, right]) => left === right).length / pairs.length;
  let expected = 0;
  for (const category of new Set(allValues)) {
    const firstRate =
      pairs.filter(([left]) => left === category).length / pairs.length;
    const secondRate =
      pairs.filter(([, right]) => right === category).length / pairs.length;
    expected += firstRate * secondRate;
  }
  if (expected >= 1) return 1;
  return (observed - expected) / (1 - expected);
}

function annotatorPairAgreement(itemLabelMap) {
  const pairData = new Map();
  for (const entries of itemLabelMap.values()) {
    if (entries.length < 2) continue;
    for (const [left, right] of combinations(entries)) {
      const key = [String(left.annotator_id), String(right.annotator_id)]
        .sort()
        .join("::");
      if (!pairData.has(key)) pairData.set(key, { agree: 0, total: 0 });
      const data = pairData.get(key);
      data.total += 1;
      if (
        normalizeAgreementValue(left.value) ===
        normalizeAgreementValue(right.value)
      ) {
        data.agree += 1;
      }
    }
  }
  return Array.from(pairData.entries()).map(([key, data]) => {
    const [annotator1, annotator2] = key.split("::");
    return {
      annotator_1_id: annotator1,
      annotator_2_id: annotator2,
      agreement_pct: data.total > 0 ? round3(data.agree / data.total) : 0,
      total_comparisons: data.total,
    };
  });
}

function normalizeAgreementValue(value) {
  if (Array.isArray(value)) {
    return pythonRepr([...value].sort());
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value)
      .map(([key, entryValue]) => [
        key,
        Array.isArray(entryValue) ? [...entryValue].sort() : entryValue,
      ])
      .sort(([left], [right]) => left.localeCompare(right));
    return `[${entries
      .map(
        ([key, entryValue]) =>
          `(${pythonRepr(key)}, ${pythonRepr(entryValue)})`,
      )
      .join(", ")}]`;
  }
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

function pythonValueKey(value) {
  return typeof value === "string" ? value : pythonRepr(value);
}

function pythonRepr(value) {
  if (value === null || value === undefined) return "None";
  if (typeof value === "string") return `'${value.replaceAll("'", "\\'")}'`;
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return `[${value.map(pythonRepr).join(", ")}]`;
  if (typeof value === "object") {
    return `{${Object.entries(value)
      .map(
        ([key, entryValue]) => `${pythonRepr(key)}: ${pythonRepr(entryValue)}`,
      )
      .join(", ")}}`;
  }
  return String(value);
}

function round1(value) {
  return Math.round(Number(value) * 10) / 10;
}

function round3(value) {
  return Math.round(Number(value) * 1000) / 1000;
}

function nearlyEqual(left, right, epsilon = 0.0001) {
  return Math.abs(Number(left) - Number(right)) <= epsilon;
}

function nullableNearlyEqual(left, right, epsilon = 0.0001) {
  if (
    left === null ||
    left === undefined ||
    right === null ||
    right === undefined
  ) {
    return left === right;
  }
  return nearlyEqual(left, right, epsilon);
}

async function findQueueEntryForSource(client, queueId, sourceType, sourceId) {
  const entries = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: {
        source_type: sourceType,
        source_id: sourceId,
      },
    }),
  );
  return entries.find((entry) => String(entry?.queue?.id) === String(queueId));
}

async function findDefaultQueueEntryForSource(
  client,
  queueId,
  sourceType,
  sourceId,
) {
  const entries = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/for-source/"), {
      query: {
        sources: JSON.stringify([
          {
            source_type: sourceType,
            source_id: sourceId,
          },
        ]),
      },
    }),
  );
  return entries.find(
    (entry) =>
      String(entry?.queue?.id) === String(queueId) &&
      entry?.queue?.is_default === true,
  );
}

async function resolveDefaultQueueForDirectTraceAnnotation(
  client,
  seedSample,
  evidence,
) {
  const queues = asArray(
    await client.get(apiPath("/model-hub/annotation-queues/"), {
      query: { limit: 200, include_counts: true },
    }),
  );

  for (const candidate of queues) {
    if (
      !candidate?.id ||
      candidate.status !== "active" ||
      !candidate.is_default
    ) {
      continue;
    }
    const detail = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", { id: candidate.id }),
    );
    if (!detail?.is_default || detail.status !== "active") continue;
    const projectId = relatedId(detail.project);
    if (!projectId) continue;

    const source = await firstTraceSourceForDefaultQueue(
      client,
      detail.id,
      projectId,
      seedSample,
    );
    if (!source) continue;

    evidence.push({
      default_queue_selection: "existing-project-default",
      queue_id: detail.id,
      queue_name: detail.name,
      trace_project_id: projectId,
      trace_id: source.traceId,
    });
    return {
      queue: detail,
      source,
      action: "existing",
    };
  }

  let defaultQueuePayload;
  try {
    defaultQueuePayload = await client.post(
      apiPath("/model-hub/annotation-queues/get-or-create-default/"),
      { project_id: seedSample.projectId },
    );
  } catch (error) {
    if (error?.status === 402) {
      skip(
        "No existing project default queue with a trace source is available, and local queue quota blocks creating one.",
      );
    }
    throw error;
  }
  const queue = defaultQueuePayload?.queue || defaultQueuePayload;
  evidence.push({
    default_queue_selection: "get-or-create",
    queue_id: queue?.id || null,
    queue_action: defaultQueuePayload?.action || null,
    trace_project_id: seedSample.projectId,
    trace_id: seedSample.traceId,
  });
  return {
    queue,
    source: seedSample,
    action: defaultQueuePayload?.action || null,
  };
}

async function firstTraceSourceForDefaultQueue(
  client,
  queueId,
  projectId,
  seedSample,
) {
  const candidates = [];
  const seen = new Set();
  const pushCandidate = (candidate) => {
    if (!candidate?.traceId || seen.has(String(candidate.traceId))) return;
    seen.add(String(candidate.traceId));
    candidates.push(candidate);
  };

  if (String(seedSample?.projectId) === String(projectId)) {
    pushCandidate(seedSample);
  }

  let list;
  try {
    list = await client.get(apiPath("/tracer/trace/list_traces_of_session/"), {
      query: {
        project_id: projectId,
        page_number: 0,
        page_size: 25,
      },
    });
  } catch {
    return null;
  }
  for (const trace of asArray(list.table || list)) {
    const traceId = trace.trace_id || trace.id;
    if (!traceId) continue;
    pushCandidate({
      traceId,
      spanId: seedSample?.spanId,
      projectId,
    });
  }

  let fallback = null;
  for (const candidate of candidates) {
    const entry = await findDefaultQueueEntryForSource(
      client,
      queueId,
      "trace",
      candidate.traceId,
    );
    if (!entry) continue;
    const result = { ...candidate, entry };
    if (!entry.item?.id) return result;
    fallback ||= result;
  }

  return fallback;
}

async function resolveDefaultQueueTraceSource(
  client,
  queueId,
  seedSample,
  evidence,
) {
  const candidates = [];
  const seen = new Set();
  const pushCandidate = (candidate) => {
    if (!candidate?.traceId || seen.has(String(candidate.traceId))) return;
    seen.add(String(candidate.traceId));
    candidates.push(candidate);
  };

  pushCandidate(seedSample);
  if (seedSample?.projectId) {
    const list = await client.get(
      apiPath("/tracer/trace/list_traces_of_session/"),
      {
        query: {
          project_id: seedSample.projectId,
          page_number: 0,
          page_size: 25,
        },
      },
    );
    for (const trace of asArray(list.table || list)) {
      const traceId = trace.trace_id || trace.id;
      if (!traceId) continue;
      pushCandidate({
        traceId,
        spanId: seedSample.spanId,
        projectId: seedSample.projectId || relatedId(trace.project),
      });
    }
  }

  let fallback = null;
  for (const candidate of candidates) {
    const entry = await findDefaultQueueEntryForSource(
      client,
      queueId,
      "trace",
      candidate.traceId,
    );
    if (!entry) continue;
    if (!asArray(entry.labels).length) continue;
    const result = { ...candidate, entry };
    if (!entry.item?.id) {
      evidence.push({
        default_queue_source_state: "unqueued-before-direct-score",
        queue_id: queueId,
        trace_id: candidate.traceId,
        trace_project_id: candidate.projectId,
      });
      return result;
    }
    fallback ||= result;
  }

  if (fallback) {
    evidence.push({
      default_queue_source_state: "preexisting-item-before-direct-score",
      queue_id: queueId,
      trace_id: fallback.traceId,
      trace_project_id: fallback.projectId,
      queue_item_id: fallback.entry?.item?.id,
    });
    return fallback;
  }

  skip(
    "No trace source in the selected project returned the default queue and temporary label.",
  );
}

async function expectItemMissing(client, queueId, itemId) {
  try {
    await client.get(
      queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/",
        queueId,
        { id: itemId },
      ),
    );
  } catch (error) {
    if (error.status === 404) return;
    throw error;
  }
  throw new Error(`Queue item ${itemId} was still readable after deletion.`);
}

async function deleteScoreIfPresent(client, scoreId) {
  if (!scoreId) return;
  try {
    await client.delete(apiPath("/model-hub/scores/{id}/", { id: scoreId }));
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function deleteQueueItemIfPresent(client, queueId, itemId) {
  try {
    await client.delete(
      queuePath(
        "/model-hub/annotation-queues/{queue_id}/items/{id}/",
        queueId,
        { id: itemId },
      ),
    );
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function deleteAutomationRuleIfPresent(client, queueId, ruleId) {
  try {
    await client.delete(
      queuePath(
        "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/",
        queueId,
        { id: ruleId },
      ),
    );
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function removeQueueLabelIfPresent(client, queueId, labelIdValue) {
  try {
    await client.post(
      apiPath("/model-hub/annotation-queues/{id}/remove-label/", {
        id: queueId,
      }),
      { label_id: labelIdValue },
    );
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function deleteAnnotationLabelIfPresent(client, labelIdValue) {
  try {
    await client.delete(
      apiPath("/model-hub/annotations-labels/{id}/", {
        id: labelIdValue,
      }),
    );
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function resolveObserveVoiceCallSource(client, evidence) {
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
      withQuery(apiPath("/tracer/trace/list_voice_calls/"), {
        project_id: project.id,
        page: 1,
        page_size: 25,
        filters: JSON.stringify([]),
      }),
    );
    const baseRows = asArray(list).filter((row) => row?.trace_id);
    if (!baseRows.length) continue;

    for (const call of baseRows) {
      const traceId = call.trace_id;
      if (!traceId) continue;
      const detail = await client.get(
        apiPath("/tracer/trace/voice_call_detail/"),
        {
          query: { trace_id: traceId },
        },
      );
      const rootSpan = findVoiceRootConversationSpan(detail?.observation_span);
      if (!rootSpan?.id) continue;
      if (!isTerminalTraceRootSpan(rootSpan)) {
        evidence.push({
          endpoint: "annotation queue voice source skipped",
          project_id: project.id,
          trace_id: traceId,
          root_span_id: rootSpan.id,
          root_span_status: rootSpan.status || null,
          reason: "trace in progress",
        });
        continue;
      }

      evidence.push({
        endpoint: "annotation queue voice source search",
        project_id: project.id,
        trace_id: traceId,
        root_span_id: rootSpan.id,
        root_span_status: rootSpan.status || null,
        voice_rows: baseRows.length,
      });
      return {
        project,
        call,
        detail,
        traceId,
        rootSpanId: rootSpan.id,
        baseRows,
      };
    }
  }

  skip("No observe voice call is available for queue add-items coverage.");
}

function findVoiceRootConversationSpan(spans) {
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

function isTerminalTraceRootSpan(span) {
  return ["OK", "ERROR"].includes(String(span?.status || "").toUpperCase());
}

async function resolveObserveSessionSource(client, evidence) {
  const knownProjectId = "ca3938dd-8a85-5c57-bc51-0faa60882d6d";
  const preferredProjectId =
    process.env.OBSERVE_SESSIONS_PROJECT_ID || process.env.OBSERVE_PROJECT_ID;
  const projects = preferredProjectId
    ? [{ id: preferredProjectId, name: "env observe sessions project" }]
    : [
        { id: knownProjectId, name: "known observe sessions project" },
        ...asArray(
          await client.get(apiPath("/tracer/project/list_projects/"), {
            query: { page_number: 0, page_size: 100 },
          }),
        ),
      ];
  const seen = new Set();

  for (const project of projects) {
    if (!project?.id || seen.has(String(project.id))) continue;
    seen.add(String(project.id));
    let list;
    try {
      list = await client.get(
        withQuery(apiPath("/tracer/trace-session/list_sessions/"), {
          project_id: project.id,
          page_number: 0,
          page_size: 25,
          filters: JSON.stringify([]),
        }),
      );
    } catch {
      continue;
    }
    const rows = asArray(list).filter((row) => row?.session_id);
    if (!rows.length) continue;
    const knownSession = rows.find(
      (row) => row.session_id === "e171945a-fbaa-51cd-b173-c893e47090d6",
    );
    const session = knownSession || rows[0];
    evidence.push({
      endpoint: "annotation queue observe session source search",
      project_id: project.id,
      session_id: session.session_id,
      session_rows: rows.length,
    });
    return {
      project,
      sessionId: session.session_id,
      baseRows: rows,
    };
  }

  skip("No observe session row is available for filter-mode queue coverage.");
}

async function hardDeleteQueueIfPresent(client, queueId, queueName) {
  const hardDelete = () =>
    client.post(
      apiPath("/model-hub/annotation-queues/{id}/hard-delete/", {
        id: queueId,
      }),
      { force: true, confirm_name: queueName },
    );
  try {
    await hardDelete();
    return;
  } catch (error) {
    if (error.status !== 404) throw error;
  }

  try {
    await client.post(
      apiPath("/model-hub/annotation-queues/{id}/restore/", {
        id: queueId,
      }),
      {},
    );
  } catch (error) {
    if (error.status === 404) return;
    throw error;
  }

  try {
    await hardDelete();
  } catch (error) {
    if (error.status !== 404) throw error;
  }
}

async function resolvePreseededDisposableArchiveQueue(client, evidence) {
  const queueId = process.env.API_JOURNEY_ARCHIVE_QUEUE_ID;
  if (!queueId) {
    skip(
      "Queue creation is plan-limited; set API_JOURNEY_ARCHIVE_QUEUE_ID to a disposable api journey queue.",
    );
  }

  const queue = await client.get(
    apiPath("/model-hub/annotation-queues/{id}/", { id: queueId }),
  );
  const name = String(queue?.name || "");
  assert(
    name.startsWith("api journey"),
    `Refusing to archive/hard-delete non-disposable queue ${queueId} (${name}).`,
  );
  evidence.push({
    queue_create_blocked: "ENTITLEMENT_LIMIT",
    preseeded_archive_queue_id: queueId,
  });
  return queue;
}

async function deleteDatasetIfPresent(client, datasetId) {
  try {
    await client.request(
      "DELETE",
      apiPath("/model-hub/develops/delete_dataset/"),
      {
        body: { dataset_ids: [datasetId] },
      },
    );
  } catch (error) {
    if (![400, 404].includes(error.status)) throw error;
  }
}

async function createDisposableDatasetWithRows(
  client,
  cleanup,
  runId,
  evidence,
) {
  const preseededDatasetId = process.env.API_JOURNEY_DATASET_ID;
  if (preseededDatasetId) {
    return resolvePreseededDisposableDatasetWithRows(
      client,
      cleanup,
      preseededDatasetId,
      evidence,
    );
  }

  const datasetName = `api journey queue source dataset ${runId}`;
  let created;
  try {
    created = await client.post(
      apiPath("/model-hub/develops/create-empty-dataset/"),
      {
        new_dataset_name: datasetName,
        model_type: "generative_llm",
        row: 0,
      },
    );
  } catch (error) {
    if (error?.status === 429 || error?.status === 402) {
      skip(
        "Dataset creation is plan-limited; dataset-row add-to-queue coverage needs a disposable dataset.",
      );
    }
    throw error;
  }

  const datasetId = created?.dataset_id || created?.id;
  assert(datasetId, "Create empty dataset did not return dataset_id.");
  cleanup.defer("delete dataset row journey dataset", () =>
    deleteDatasetIfPresent(client, datasetId),
  );

  const columnName = `api_journey_queue_source_${runId.replace(
    /[^a-z0-9]/gi,
    "_",
  )}`;
  await client.post(
    apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
      dataset_id: datasetId,
    }),
    {
      new_column_name: columnName,
      column_type: "text",
      source: "OTHERS",
    },
  );

  let table = await getDatasetTable(client, datasetId, {
    column_config_only: true,
  });
  const column = findDatasetColumn(table, columnName);
  assert(column?.id, "Disposable dataset column was not visible after reload.");

  await client.post(
    apiPath("/model-hub/develops/{dataset_id}/add_empty_rows/", {
      dataset_id: datasetId,
    }),
    { num_rows: 2 },
  );
  table = await getDatasetTable(client, datasetId);
  const rows = firstDatasetRows(table, 2);
  assert(
    rows.length === 2,
    "Disposable dataset rows were not visible after add.",
  );

  for (const [index, row] of rows.entries()) {
    const value = `queue source row ${index + 1} ${runId}`;
    await client.post(
      apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
        dataset_id: datasetId,
      }),
      {
        row_id: row.row_id,
        column_id: column.id,
        new_value: value,
      },
    );
  }

  const reloaded = await getDatasetTable(client, datasetId);
  const reloadedRows = firstDatasetRows(reloaded, 2);
  for (const [index, row] of reloadedRows.entries()) {
    const expected = `queue source row ${index + 1} ${runId}`;
    assert(
      cellValueFor(row, column.id) === expected,
      `Disposable dataset cell ${row.row_id} did not round-trip before queue add.`,
    );
  }

  evidence.push({
    dataset_creation: "created",
    dataset_id: datasetId,
    dataset_column_id: column.id,
    dataset_row_ids: reloadedRows.map((row) => row.row_id),
  });
  return {
    id: datasetId,
    columnId: column.id,
    rowIds: reloadedRows.map((row) => String(row.row_id)),
  };
}

async function resolvePreseededDisposableDatasetWithRows(
  client,
  cleanup,
  datasetId,
  evidence,
) {
  const table = await getDatasetTable(client, datasetId);
  const dataset = await findDatasetSummary(client, datasetId);
  const datasetName = String(dataset?.name || table?.name || "");
  assert(
    datasetName.startsWith("api journey"),
    `Refusing to delete/use non-disposable dataset ${datasetId} (${datasetName}).`,
  );

  const rows = firstDatasetRows(table, 2);
  assert(
    rows.length >= 2,
    "Preseeded dataset must contain at least two visible rows.",
  );
  const column = asArray(table?.column_config).find(
    (candidate) => candidate?.id,
  );
  assert(
    column?.id,
    "Preseeded dataset must contain at least one visible column.",
  );
  cleanup.defer("delete preseeded dataset row journey dataset", () =>
    deleteDatasetIfPresent(client, datasetId),
  );
  evidence.push({
    dataset_creation: "preseeded",
    dataset_id: datasetId,
    dataset_name: datasetName,
    dataset_column_id: column.id,
    dataset_row_ids: rows.map((row) => row.row_id),
  });
  return {
    id: datasetId,
    columnId: column.id,
    rowIds: rows.map((row) => String(row.row_id)),
  };
}

async function getDatasetTable(client, datasetId, query = {}) {
  return client.get(
    apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
      dataset_id: datasetId,
    }),
    {
      query: {
        page_size: 25,
        current_page_index: 0,
        ...query,
      },
    },
  );
}

async function findDatasetSummary(client, datasetId) {
  const datasets = asArray(
    await client.get(apiPath("/model-hub/develops/get-datasets/"), {
      query: { page: 0, page_size: 100 },
    }),
  );
  return datasets.find((dataset) => String(dataset?.id) === String(datasetId));
}

function findDatasetColumn(tablePayload, name) {
  return asArray(tablePayload?.column_config).find(
    (column) => column.name === name,
  );
}

function firstDatasetRows(tablePayload, count) {
  return asArray(tablePayload?.table)
    .filter((row) => row?.row_id)
    .slice(0, count);
}

function cellValueFor(row, columnId) {
  return row?.[columnId]?.cell_value;
}

async function expectHttpStatus(fn, expectedStatus) {
  try {
    await fn();
  } catch (error) {
    if (error.status === expectedStatus) return error.status;
    throw error;
  }
  throw new Error(`Expected HTTP ${expectedStatus}, but request succeeded.`);
}

async function expectHttpError(fn, expectedStatus, expectedText = "") {
  try {
    await fn();
  } catch (error) {
    if (error.status !== expectedStatus) throw error;
    if (expectedText) {
      const bodyText = JSON.stringify(error.body || {});
      assert(
        bodyText.includes(expectedText),
        `Expected HTTP ${expectedStatus} body to include "${expectedText}", got ${bodyText}.`,
      );
    }
    return { status: error.status, body: error.body };
  }
  throw new Error(`Expected HTTP ${expectedStatus}, but request succeeded.`);
}

function sameJsonValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function labelId(label) {
  return label?.label_id || label?.id || null;
}

function reviewAnnotationValue(label, runId, suffix) {
  const type = String(label?.type || "").toLowerCase();
  if (type.includes("text")) return `review ${suffix} ${runId}`;
  if (type.includes("thumb")) return { value: "up" };
  return annotationValueForLabel(label);
}

function revisedReviewAnnotationValue(label, originalValue, runId) {
  const type = String(label?.type || "").toLowerCase();
  const settings = label?.settings || {};
  if (type.includes("text")) return `review revised ${runId}`;
  if (type.includes("thumb")) {
    return { value: originalValue?.value === "up" ? "down" : "up" };
  }
  if (type.includes("numeric") || type.includes("number")) {
    const max = Number.isFinite(Number(settings.max))
      ? Number(settings.max)
      : 10;
    const min = Number.isFinite(Number(settings.min))
      ? Number(settings.min)
      : 0;
    const next = Number(originalValue) + 1;
    return next <= max ? next : min;
  }
  if (type.includes("star")) {
    const max = Number.isFinite(Number(settings.no_of_stars))
      ? Number(settings.no_of_stars)
      : 5;
    return Number(originalValue) < max ? Number(originalValue) + 1 : 1;
  }
  if (type.includes("categorical") || type.includes("select")) {
    const options = firstArray(
      settings.options,
      settings.choices,
      settings.categories,
      settings.values,
    );
    const values = options.map(
      (option) =>
        option?.value ?? option?.id ?? option?.label ?? option?.name ?? option,
    );
    return (
      values.find((value) => !sameJsonValue(value, originalValue)) ||
      `${String(originalValue)} revised ${runId}`
    );
  }
  return `review revised ${runId}`;
}

function firstArray(...values) {
  return values.find((value) => Array.isArray(value)) || [];
}

async function resolveTraceAndSpanSample(client, fixture = {}) {
  const projects = asArray(
    await client.get(apiPath("/tracer/project/list_projects/"), {
      query: { page_number: 0, page_size: 25 },
    }),
  );
  const accessibleProjectIds = new Set(
    projects.map((project) => String(project?.id || "")).filter(Boolean),
  );

  const preferredTraceId = process.env.OBSERVE_TRACE_ID;
  const preferredSpanId = process.env.OBSERVE_SPAN_ID;
  if (preferredTraceId && preferredSpanId) {
    try {
      const detail = await client.get(
        apiPath("/tracer/trace/{id}/", { id: preferredTraceId }),
      );
      const projectId = traceProjectId(detail);
      const spans = flattenTraceEntries(detail);
      if (
        projectId &&
        accessibleProjectIds.has(String(projectId)) &&
        spans.some((row) => String(row.spanId) === preferredSpanId)
      ) {
        return {
          traceId: preferredTraceId,
          spanId: preferredSpanId,
          projectId,
        };
      }
    } catch {
      // Fall back to discovery below.
    }
  }

  for (const project of projects) {
    if (!project?.id) continue;
    let list;
    try {
      list = await client.get(
        apiPath("/tracer/trace/list_traces_of_session/"),
        {
          query: {
            project_id: project.id,
            page_number: 0,
            page_size: 10,
          },
        },
      );
    } catch (error) {
      fixture.evidence?.push?.({
        trace_sample_discovery_skipped_project_id: project.id,
        trace_sample_discovery_status: error.status,
      });
      continue;
    }
    for (const trace of asArray(list.table || list)) {
      const traceId = trace.trace_id || trace.id;
      if (!traceId) continue;
      try {
        const detail = await client.get(
          apiPath("/tracer/trace/{id}/", { id: traceId }),
        );
        const span = flattenTraceEntries(detail).find((row) => row.spanId);
        if (span?.spanId) {
          return {
            traceId,
            spanId: span.spanId,
            projectId: traceProjectId(detail) || relatedId(trace.project),
          };
        }
      } catch {
        // Try the next trace.
      }
    }
  }

  if (
    fixture.cleanup &&
    fixture.organizationId &&
    fixture.workspaceId &&
    fixture.userId &&
    fixture.runId
  ) {
    const namePrefix = `api journey trace sample ${fixture.runId}`;
    fixture.cleanup.defer("hard-delete trace sample DB fixture", () =>
      deleteTraceSpanFixtureDb(namePrefix),
    );
    const sample = await insertTraceSpanSampleFixtureDb({
      namePrefix,
      organizationId: fixture.organizationId,
      workspaceId: fixture.workspaceId,
      userId: fixture.userId,
    });
    fixture.evidence?.push?.({
      trace_sample_fixture_mode:
        "db_seeded_after_empty_or_unavailable_trace_api",
      trace_sample_project_id: sample.project_id,
      trace_sample_trace_id: sample.trace_id,
      trace_sample_span_id: sample.span_id,
      trace_sample_span_ids: sample.span_ids,
    });
    return {
      traceId: sample.trace_id,
      spanId: sample.span_id,
      spanIds: asArray(sample.span_ids),
      projectId: sample.project_id,
    };
  }

  skip(
    "No trace with a resolvable observation span is available for disposable queue coverage.",
  );
}

function traceProjectId(detail) {
  return relatedId(detail?.trace?.project || detail?.project);
}

function relatedId(value) {
  if (!value) return null;
  if (typeof value === "string") return value;
  return value.id || value.project_id || value.uuid || null;
}

function flattenTraceEntries(detail) {
  const roots = asArray(detail?.observation_spans).length
    ? asArray(detail.observation_spans)
    : [detail?.root || detail?.data || detail?.trace || detail];
  const rows = [];
  function walk(entry) {
    if (!entry || typeof entry !== "object") return;
    const span = entry.observation_span || entry.span || entry;
    const spanId = span?.id || span?.span_id;
    rows.push({ entry, spanId });
    for (const child of asArray(entry.children)) walk(child);
  }
  for (const root of roots) walk(root);
  return rows;
}
