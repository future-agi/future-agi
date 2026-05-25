import process from "node:process";

import {
  apiPath,
  asArray,
  assert,
  currentUserId,
  skip,
  withQuery,
} from "./api-client.mjs";

export function queuePath(template, queueId, params = {}) {
  return apiPath(template, { queue_id: queueId, id: params.id, ...params });
}

export async function resolveQueue(client, evidence) {
  if (process.env.ANNOTATION_QUEUE_ID) {
    const queue = await client.get(
      apiPath("/model-hub/annotation-queues/{id}/", {
        id: process.env.ANNOTATION_QUEUE_ID,
      }),
    );
    evidence.push({
      endpoint: "annotation queue detail",
      queue_id: process.env.ANNOTATION_QUEUE_ID,
    });
    return queue;
  }

  const queuesPayload = await client.get(
    apiPath("/model-hub/annotation-queues/"),
    {
      query: { limit: 25 },
    },
  );
  const queues = asArray(queuesPayload);
  const queue =
    queues.find((item) => item.status === "active" && item.item_count > 0) ||
    queues.find((item) => item.item_count > 0) ||
    queues[0];
  if (!queue?.id) {
    skip("No annotation queue exists for this account/workspace.");
  }
  evidence.push({ endpoint: "annotation queue list", queue_id: queue.id });
  return queue;
}

export async function resolveQueueItem(client, queueId, evidence, query = {}) {
  const itemsPayload = await client.get(
    queuePath("/model-hub/annotation-queues/{queue_id}/items/", queueId),
    { query: { limit: 25, ...query } },
  );
  const items = asArray(itemsPayload);
  const item = items.find((row) => row?.id);
  if (!item?.id) {
    skip(`No queue items found for queue ${queueId}.`);
  }
  evidence.push({ endpoint: "queue item list", item_id: item.id });
  return item;
}

export async function resolveObserveProject(client, evidence) {
  if (process.env.OBSERVE_PROJECT_ID) {
    evidence.push({
      endpoint: "observe project env",
      project_id: process.env.OBSERVE_PROJECT_ID,
    });
    return { id: process.env.OBSERVE_PROJECT_ID };
  }

  const payload = await client.get(apiPath("/tracer/project/list_projects/"), {
    query: { project_type: "observe", page_number: 0, page_size: 25 },
  });
  const projects = asArray(payload);
  const project =
    projects.find((row) => Number(row.last_30_days_vol || 0) > 0) ||
    projects[0];
  if (!project?.id) {
    skip("No observe project exists for this account/workspace.");
  }
  evidence.push({ endpoint: "observe project list", project_id: project.id });
  return project;
}

export async function getQueueLabels(client, queueId, itemId) {
  const detail = await client.get(
    queuePath(
      "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
      queueId,
      { id: itemId },
    ),
    {
      query: {
        include_completed: true,
        include_all_annotations: true,
      },
    },
  );
  return { detail, labels: asArray(detail.labels) };
}

export function annotationValueForLabel(label) {
  const type = String(label?.type || "").toLowerCase();
  const settings = label?.settings || {};
  const options = firstArray(
    settings.options,
    settings.choices,
    settings.categories,
    settings.values,
  );
  if (type.includes("numeric") || type.includes("number")) return 3;
  if (type.includes("star")) return 4;
  if (type.includes("thumb")) return { value: "up" };
  if (type.includes("categorical") || type.includes("select")) {
    const option = options[0];
    return (
      option?.value ??
      option?.id ??
      option?.label ??
      option?.name ??
      option ??
      ""
    );
  }
  return `api journey ${Date.now().toString(36)}`;
}

export function canonicalNumberFilter(columnId, filterOp, filterValue) {
  return [
    {
      column_id: columnId,
      filter_config: {
        filter_type: "number",
        filter_op: filterOp,
        filter_value: filterValue,
      },
    },
  ];
}

export function canonicalTextFilter(columnId, filterOp, filterValue) {
  return [
    {
      column_id: columnId,
      filter_config: {
        filter_type: "text",
        filter_op: filterOp,
        filter_value: filterValue,
      },
    },
  ];
}

export function assertNoCamelGenAi(fields) {
  const bad = fields.filter((field) => String(field).includes("genAi"));
  assert(
    bad.length === 0,
    `Attribute inventory contains camelCase genAi keys: ${bad.slice(0, 5).join(", ")}`,
  );
}

export function queryWithFilters(pathName, filters, extra = {}) {
  return withQuery(pathName, { ...extra, filters: JSON.stringify(filters) });
}

export function assertCurrentUserResolved(user) {
  const id = currentUserId(user);
  assert(
    id,
    "Current user id could not be resolved from /accounts/user-info/.",
  );
  return id;
}

function firstArray(...values) {
  return values.find((value) => Array.isArray(value)) || [];
}
