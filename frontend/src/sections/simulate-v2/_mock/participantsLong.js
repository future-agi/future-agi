/*
  A long multi-agent call for the Participants tab (Chintan, 17 Nov: "20–30
  steps and loops"): 5 caller turns, 26 steps, two loops — a refund tool
  retried five times, and Front desk asking Escalations for approval four
  times while the queue stays busy. Open a run's Participants tab with
  ?participants=long to see it.
*/

const call = (id, name, at, resultLabel, extra = {}) => ({
  id,
  name,
  at,
  resultLabel,
  recorded: true,
  durationMs: 400,
  input: extra.input ?? null,
  output: extra.output ?? { status: resultLabel },
  ...extra,
});

const worker = (
  id,
  name,
  delegatedAt,
  endedAt,
  label,
  answerLabel,
  extra = {},
) => ({
  id,
  name,
  recorded: true,
  state: "done",
  delegatedAt,
  endedAt,
  label,
  answerLabel,
  task: `${label}.`,
  result: `${answerLabel}.`,
  calls: [],
  ...extra,
});

const COORD = { id: "m-front", name: "Front desk" };
const REFUNDS = { id: "m-refunds", name: "Refunds specialist" };
const ESC = { id: "m-esc", name: "Escalations" };
const SHIP = { id: "m-ship", name: "Shipping specialist" };

/* Turn 3: four rounds of "get supervisor approval" → check_queue → busy. */
const approvalRounds = [0, 1, 2, 3].map((i) =>
  worker(
    ESC.id,
    ESC.name,
    40 + i * 6,
    43.5 + i * 6,
    "Get supervisor approval",
    "Queue busy, try again",
    {
      calls: [
        call(`c-queue-${i}`, "check_queue", 41 + i * 6, "Busy · 12 waiting", {
          input: { queue: "supervisors" },
        }),
      ],
    },
  ),
);

export const LONG_CALL = {
  observed: true,
  summary:
    "Front desk passed this call to 3 workers over 5 turns. Refunds specialist couldn't issue the refund; the rest finished.",
  coordinator: COORD,
  workers: [REFUNDS, ESC, SHIP],
  notRun: [],
  notObserved: [],
  turns: [
    {
      n: 1,
      at: 0,
      request: "I want a refund for order 9450.",
      reply: "I can help — the refund is over $200, so it needs a check.",
      replyAt: 9,
      workers: [
        worker(
          REFUNDS.id,
          REFUNDS.name,
          1.5,
          7.5,
          "Check refund eligibility",
          "Eligible, over $200",
          {
            calls: [
              call("c-order", "get_order", 2.5, "Found · $240", {
                input: { order_id: 9450 },
              }),
              call(
                "c-policy",
                "check_refund_policy",
                4,
                "Needs approval over $200",
              ),
              /* The world says no, with its reason (Karthik, 17 Nov). */
              call("c-refund-try", "issue_refund", 5.5, "Refused", {
                refused: true,
                input: { order_id: 9450, amount: 240 },
                output: {
                  status: "refused",
                  reason: "Refunds over $200 need a supervisor's sign-off",
                },
              }),
            ],
          },
        ),
      ],
      own: [],
    },
    {
      n: 2,
      at: 12,
      request: "It arrived damaged. Just refund it now, please.",
      reply: "I couldn't issue the refund — the payments system kept failing.",
      replyAt: 33,
      workers: [
        worker(
          REFUNDS.id,
          REFUNDS.name,
          13,
          31,
          "Issue the refund",
          "Couldn't issue the refund",
          {
            failed: true,
            state: "failed",
            calls: [0, 1, 2, 3, 4].map((i) =>
              call(
                `c-refund-${i}`,
                "issue_refund",
                14 + i * 3.4,
                "Failed · payments timeout",
                {
                  failed: true,
                  input: { order_id: 9450, amount: 240 },
                  output: { error: "payments timeout" },
                },
              ),
            ),
          },
        ),
      ],
      own: [],
    },
    {
      n: 3,
      at: 36,
      request: "Can a supervisor approve it then?",
      reply: "The supervisors are busy right now — I couldn't get approval.",
      replyAt: 66,
      workers: approvalRounds,
      own: [],
    },
    {
      n: 4,
      at: 70,
      request: "Fine. What about store credit and a pickup?",
      reply: "Done — $240 store credit, and a courier picks it up tomorrow.",
      replyAt: 84,
      workers: [
        worker(
          REFUNDS.id,
          REFUNDS.name,
          71,
          75.5,
          "Offer store credit",
          "$240 credit issued",
          {
            calls: [
              call("c-credit", "issue_store_credit", 72, "Issued · $240"),
            ],
          },
        ),
        worker(
          SHIP.id,
          SHIP.name,
          76,
          82,
          "Arrange a pickup",
          "Pickup booked for tomorrow",
          {
            calls: [
              call(
                "c-pickup",
                "schedule_pickup",
                77,
                "Booked · tomorrow 10–12",
              ),
            ],
          },
        ),
      ],
      own: [],
    },
    {
      n: 5,
      at: 88,
      request: "Okay, thanks.",
      reply: "You're welcome — anything else?",
      replyAt: 90,
      workers: [],
      own: [],
    },
  ],
};
