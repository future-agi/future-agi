// The events a webhook can subscribe to, and how one delivery reads.
//
// Every event offered is produced. Request logging creates request.completed,
// error.occurred, guardrail.triggered and budget.exceeded (futureagi/agentcc/
// services/log_ingestion.py); a batch job sends batch.completed once when it
// finishes (services/batch_jobs.py). An event marked produced: false would be
// left out of a new webhook's choices and named as never sent; none is.
//
// A failed attempt is put back to "pending" with its next retry time: 30 s
// after the first failure, doubling after each one. The delivery schedule
// (services/webhook_delivery.py, every 30 s) sends what is due, until the
// event is delivered or its attempts run out (dead letter, "Gave up"). Retry
// resets an event and starts its delivery at once.

// The delivery list endpoint answers the newest 100 deliveries, with no
// paging (futureagi/agentcc/views/webhook_outbound.py).
export const DELIVERY_PAGE_LIMIT = 100;

export const WEBHOOK_EVENTS = [
  {
    value: "request.completed",
    label: "Request completed",
    description: "Every request the gateway logs.",
    produced: true,
  },
  {
    value: "error.occurred",
    label: "Error occurred",
    description: "A request that ended in an error.",
    produced: true,
  },
  {
    value: "guardrail.triggered",
    label: "Guardrail triggered",
    // NEW: site claims review required for this event description.
    description:
      "A guardrail flagged or blocked a request. Only requests handled while guardrails are on produce this event.",
    produced: true,
  },
  {
    value: "budget.exceeded",
    label: "Budget exceeded",
    description: "A budget limit was reached.",
    produced: true,
  },
  {
    value: "batch.completed",
    label: "Batch completed",
    description: "A batch job finished.",
    produced: true,
  },
];

// RL-917: offer every produced event regardless of node enablement. The
// guardrail event's description explains when it fires; stored selections
// remain unchanged unless the person explicitly edits them.
export function offeredWebhookEvents() {
  return WEBHOOK_EVENTS.filter((e) => e.produced);
}

// What happens to a failed delivery, shown next to failed deliveries.
export const RETRY_NOTE =
  "A failed delivery is retried automatically: 30 seconds after the first failure, then twice as long after each one, until it is delivered or its attempts run out. Retry now sends it again at once.";

const BY_VALUE = Object.fromEntries(WEBHOOK_EVENTS.map((e) => [e.value, e]));

export function webhookEvent(value) {
  return BY_VALUE[value] || null;
}

// The not-enforced notes for the subscribed events this gateway never sends,
// in the order the events are offered.
export function unproducedNotes(events) {
  const chosen = new Set(events || []);
  return WEBHOOK_EVENTS.filter((e) => !e.produced && chosen.has(e.value)).map(
    (e) => e.note,
  );
}

// True when every subscribed event is one the gateway never sends: such a
// webhook is never called (except by Send test).
export function onlyUnproduced(events) {
  const list = events || [];
  return (
    list.length > 0 &&
    list.every((value) => BY_VALUE[value]?.produced === false)
  );
}

function failedAttempt(event) {
  return (
    event.status === "pending" &&
    ((event.attempts ?? 0) > 0 || Boolean(event.last_error))
  );
}

export function deliveryState(event) {
  const status = event?.status;
  if (status === "delivered") {
    return { tone: "healthy", label: "Delivered", awaitingRetry: false };
  }
  if (status === "failed") {
    return { tone: "error", label: "Failed", awaitingRetry: false };
  }
  if (status === "dead_letter") {
    return { tone: "error", label: "Gave up", awaitingRetry: false };
  }
  if (failedAttempt(event || {})) {
    return {
      tone: "warning",
      label: "Failed, will retry",
      awaitingRetry: true,
    };
  }
  return { tone: "pending", label: "Queued", awaitingRetry: false };
}

// The retry note is shown only next to deliveries that failed.
export function needsRetryNote(events) {
  return (events || []).some(
    (event) =>
      event.status === "failed" ||
      event.status === "dead_letter" ||
      failedAttempt(event),
  );
}

export function canMarkForRetry(event) {
  return event?.status === "failed" || event?.status === "dead_letter";
}

export function latestDeliveryByWebhook(events) {
  const latest = {};
  (events || []).forEach((event) => {
    const id = event.webhook;
    if (!id) return;
    const current = latest[id];
    if (!current || String(event.created_at) > String(current.created_at)) {
      latest[id] = event;
    }
  });
  return latest;
}
