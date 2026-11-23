/*
  What the analyzer (Omega) already saves about each issue and conversation,
  shaped for the issue page: a suggested fix with what would prove it wrong,
  a requirement checklist per conversation, the conversation's outcome,
  whether the agent recovered, a short failure type, and the input and
  output of each evidence step. Mock data, deterministic per issue.
*/

const TYPES = {
  ToolResultDropped: {
    section: "Tool use",
    cause:
      "The agent summarises from memory instead of the tool's full result, so items it didn't keep in mind drop out of the reply.",
    label: "Dropped tool result",
    requirement: "Carried every item the tool returned into the reply",
    fix: "Carry every item the tool returns into the reply, and re-read the result before summarising.",
    wrongIf:
      "items still go missing in conversations where the tool returned all of them.",
    io: {
      in: "segments: [AA 102, AA 2210 (cancelled)]",
      out: "Summary lists 1 of 2 passengers",
    },
  },
  ToolArgumentMismatch: {
    section: "Tool use",
    cause:
      "A correction the caller makes isn't written back to the state the agent builds tool arguments from, so the tool gets the value from before the correction.",
    label: "Invalid tool argument",
    requirement: "Passed the corrected value to the tool",
    fix: "Apply the caller's correction to the booking state before calling the tool, and send the corrected value.",
    wrongIf:
      "the tool still gets the old value in conversations where the correction was applied first.",
    io: { in: "time: 18:30 (caller said 19:30)", out: "Booked for 18:30" },
  },
  SkippedConfirmation: {
    section: "Tool use",
    cause:
      "Nothing tells the agent to read details back and wait for a yes, so it books as soon as it has an address.",
    label: "Skipped confirmation",
    requirement: "Read the details back and waited for a yes",
    fix: "Read the details back and wait for the caller's yes before booking.",
    wrongIf:
      "bookings still go wrong in conversations where the caller confirmed first.",
    io: { in: "address: 14 Elm St (unconfirmed)", out: "Pickup booked" },
  },
  PolicyViolation: {
    section: "Policies",
    cause:
      "The rule is in the policy text, but no step makes the agent check it before acting, so it acts on what the caller asked.",
    label: "Instruction violation",
    requirement: "Followed the policy rule before acting",
    fix: "Check the policy rule before acting, and route the case where the rule says to.",
    wrongIf:
      "the rule is still broken in conversations where the agent read it first.",
    io: { in: "policy check: (skipped)", out: "Action taken outside the rule" },
  },
  PolicyBypassError: {
    section: "Policies",
    cause:
      "The flow runs straight through; nothing makes the agent read the policy exceptions first.",
    label: "Instruction violation",
    requirement: "Read the policy exceptions before running the flow",
    fix: "Read the policy exceptions before running the cancellation flow.",
    wrongIf:
      "exceptions are still missed in conversations where the agent read them.",
    io: { in: "exceptions: (not read)", out: "Cancellation completed" },
  },
  OrderingRaceCondition: {
    section: "Tool use",
    cause:
      "The agent confirms as soon as the first check returns, without waiting for the second.",
    label: "Out-of-order steps",
    requirement: "Waited for every verification step before confirming",
    fix: "Wait for every verification step to finish before confirming anything.",
    wrongIf:
      "confirmations still go out early when both factors arrived first.",
    io: { in: "factor_2: (pending)", out: "Refund confirmed" },
  },
  StaleCacheReadError: {
    section: "Tool use",
    cause:
      "The agent answers from a cached result instead of reading the source again after it changed.",
    label: "Stale data",
    requirement: "Answered from the current source, not a cached value",
    fix: "Re-read the source after a warehouse switch instead of using the cached value.",
    wrongIf: "stale answers still appear right after a fresh read.",
    io: { in: "inventory: cached 3h ago", out: "In stock (it isn't)" },
  },
  SkippedVerification: {
    section: "Tool use",
    cause:
      "Nothing makes the agent run the check before acting, so it acts on what the caller said.",
    label: "Skipped verification",
    requirement: "Ran the check before acting",
    fix: "Run the check before acting, and stop if it fails.",
    wrongIf:
      "the action still goes wrong in conversations where the check ran first.",
    io: { in: "order_id: (empty)", out: "Refund of $240 issued" },
  },
  UnsupportedClaim: {
    section: "Closing",
    cause:
      "The agent confirms before it reads the tool's result, and has no branch for a call that failed.",
    label: "Unsupported claim",
    requirement: "Only confirmed what a tool result showed",
    fix: "Only confirm what a tool result shows, and say so when the tool failed.",
    wrongIf:
      "false confirmations continue in conversations where the tool result was read first.",
    io: { in: "create_label: error 502", out: "Agent says the label was sent" },
  },
  SelfContradiction: {
    section: "Closing",
    cause:
      "The agent restates figures from memory instead of the tool result, so two values reach the caller.",
    label: "Self-contradiction",
    requirement: "Stated each figure once, from the tool result",
    fix: "State figures once, from the tool result, and repeat that same value.",
    wrongIf:
      "figures still disagree in conversations where the result held one value.",
    io: { in: "total: $182.40", out: "Agent says $172.40" },
  },
  StaleToolResult: {
    section: "Tool use",
    cause:
      "The agent reuses an earlier tool result after the caller changed the request, instead of calling the tool again.",
    label: "Stale tool result",
    requirement: "Called the tool again after the request changed",
    fix: "Call the tool again after the caller changes the request.",
    wrongIf: "old quotes still appear after the tool was called again.",
    io: { in: "search: dates before the change", out: "Quoted the old fare" },
  },
};
const typeOf = (cluster) =>
  TYPES[cluster?.error?.type] || TYPES.PolicyViolation;

/** One suggested fix per issue, and what would prove it wrong. */
export const suggestedFixFor = (cluster) => {
  const t = typeOf(cluster);
  return {
    change: t.fix,
    wrongIf: t.wrongIf,
    failureType: t.label,
    cause: t.cause,
    section: t.section,
  };
};

/** Input and output for an evidence step; the decisive one carries the issue's own. */
export const stepIOFor = (cluster, step, failed) => {
  const t = { io: storyOf(cluster).io };
  if (step.label === "User") return {};
  if (step.isFailure) return { wentIn: t.io.in, cameOut: t.io.out };
  if (step.label === "Tool")
    return failed
      ? {}
      : {
          wentIn: t.io.in.replace(/\(.*?\)/, "(corrected)"),
          cameOut: "Result matches the request",
        };
  return step.text.startsWith("Retrieved")
    ? {
        wentIn: "account_id: A-10241",
        cameOut: failed
          ? "State loaded; correction not applied"
          : "State loaded with the correction",
      }
    : {
        wentIn: "tool result",
        cameOut: failed ? t.io.out : "Reply matches the result",
      };
};

/**
 * A conversation's findings: outcome (succeeded · failed · unclear), whether
 * the agent recovered its own mistake, the failure type, and the requirement
 * checklist (met · broken · unclear), each item with its proof step.
 */
export const conversationFindings = (cluster, i, failed, reel) => {
  const t = typeOf(cluster);
  const decisive = Math.max(
    0,
    reel.findIndex((s) => s.isFailure),
  );
  const recovered = failed && i % 7 === 3;
  const unclearOutcome = failed && !recovered && i % 6 === 1;
  const outcome =
    !failed || recovered ? "succeeded" : unclearOutcome ? "unclear" : "failed";
  const checklist = [
    { text: "Greeted the caller and understood the request", status: "met" },
    {
      text: t.requirement,
      status: failed && !recovered ? "broken" : "met",
      proofStep: decisive,
    },
    { text: "Kept the caller informed while working", status: "met" },
    {
      text: "Confirmed the outcome with the caller",
      status: failed && i % 3 === 0 ? "unclear" : "met",
      note: failed && i % 3 === 0 ? "not clear from the conversation" : null,
    },
    { text: "Ended the conversation cleanly", status: "met" },
  ];
  return {
    outcome,
    recovered: failed ? recovered : null,
    failureType: failed ? t.label : null,
    checklist,
  };
};

/**
 * Conversations that broke a requirement but never became an issue — they
 * look clean in the feed today. A few per issue's project, from its traffic.
 */
export const silentBreaksFor = (clusters) => {
  const out = [];
  clusters.forEach((c, ci) => {
    const t = typeOf(c);
    const n = (ci * 5 + 3) % 4;
    for (let k = 0; k < n; k += 1) {
      out.push({
        id: `silent-${c.cluster_id}-${k + 1}`,
        traceId: `trace-${c.cluster_id}-s${k + 1}`,
        projectId: c.project_id,
        projectName: c.project_name || c.project_id,
        requirement: t.requirement,
        failureType: t.label,
        nearIssue: c.cluster_id,
        nearIssueName: c.error?.name,
        minutesAgo: 20 + ((ci * 37 + k * 53) % 600),
      });
    }
  });
  return out;
};

/*
  The evidence story for each failure type: what the caller asked, what the
  agent did, the step where it went wrong (with what went in and came out),
  and what the caller was told. The working version shows the same request
  handled right. One story per type, so every issue reads as itself.
*/
const STORIES = {
  ToolResultDropped: {
    steps: [
      "Asked for the updated itinerary",
      "Looked up the booking",
      "Summarised the itinerary from memory",
      "Read the summary to the caller",
    ],
    tool: "get_itinerary",
    good: "Summarised every passenger the tool returned",
  },
  ToolArgumentMismatch: {
    steps: [
      "Corrected a detail halfway through",
      "Noted the request",
      "Called the booking tool with the old value",
      "Confirmed the booking to the caller",
    ],
    tool: "create_booking",
    good: "Called the booking tool with the corrected value",
  },
  SkippedConfirmation: {
    steps: [
      "Gave a pickup address",
      "Parsed the address",
      "Booked before reading the address back",
      "Told the caller the car is booked",
    ],
    tool: "book_pickup",
    good: "Read the address back and waited for a yes",
  },
  PolicyViolation: {
    steps: [
      "Asked for something the policy limits",
      "Looked up the account",
      "Acted without checking the rule",
      "Told the caller it was done",
    ],
    tool: "apply_action",
    good: "Checked the rule, then routed it where the rule says",
  },
  PolicyBypassError: {
    steps: [
      "Asked to cancel",
      "Started the cancellation flow",
      "Ran the flow without reading the exceptions",
      "Confirmed the cancellation",
    ],
    tool: "cancel_order",
    good: "Read the policy exceptions before cancelling",
  },
  OrderingRaceCondition: {
    steps: [
      "Asked for a refund",
      "Sent the second identity check",
      "Confirmed before the second check returned",
      "Told the caller the refund is on its way",
    ],
    tool: "issue_refund",
    good: "Waited for both checks, then confirmed",
  },
  StaleCacheReadError: {
    steps: [
      "Asked if an item is in stock",
      "Read the cached inventory",
      "Answered from a cache 3 hours old",
      "Told the caller it's in stock",
    ],
    tool: "get_inventory",
    good: "Read the inventory again after the warehouse switch",
  },
  SkippedVerification: {
    steps: [
      "Asked for a refund",
      "Opened the order",
      "Issued the refund without the eligibility check",
      "Told the caller the refund was issued",
    ],
    tool: "issue_refund",
    good: "Ran the eligibility check before refunding",
  },
  UnsupportedClaim: {
    steps: [
      "Asked for something to be sent or changed",
      "Called the tool",
      "The tool failed; the agent didn't read the result",
      "Told the caller it was done",
    ],
    tool: "create_label",
    good: "Read the failed result and told the caller what happened",
  },
  SelfContradiction: {
    steps: [
      "Asked for the total",
      "Fetched the invoice",
      "Restated the total from memory with a different value",
      "Gave the caller two different figures",
    ],
    tool: "get_invoice",
    good: "Stated the total once, from the invoice",
  },
  StaleToolResult: {
    steps: [
      "Changed the dates",
      "Kept the earlier search results",
      "Quoted from the search before the change",
      "Gave the caller the old price",
    ],
    tool: "search_fares",
    good: "Searched again after the dates changed",
  },
};

/*
  The type's story is one domain's. An issue whose own traces use other
  tools reads its own: the steps, the tool and what went in and came out are
  the ones its calls show (its why_shown names the tool), so a clinic issue
  never shows a refund. Issues the type's story already describes keep it.
*/
const ISSUE_STORIES = {
  "ISS-4536": { tool: "lookup_booking" },
  "ISS-4529": {
    steps: [
      "Changed the booking time halfway through",
      "Noted the request",
      "Called modify_booking with the first time",
      "Confirmed the table to the guest",
    ],
    tool: "modify_booking",
    good: "Called modify_booking with the corrected time",
    io: { in: "time: 19:00 (guest said 20:00)", out: "Table booked for 7pm" },
  },
  "ISS-4471": {
    steps: [
      "Changed the pickup address",
      "Noted the new address",
      "Called book_pickup with the old address",
      "Confirmed the pickup to the caller",
    ],
    tool: "book_pickup",
    good: "Called book_pickup with the corrected address",
    io: {
      in: "address: 8 Elm Street (caller said 12 Elm Street)",
      out: "Pickup booked to 8 Elm Street",
    },
  },
  "ISS-4489": {
    steps: [
      "Asked to cancel a car booked a minute ago",
      "Opened the booking",
      "Charged the fee before reading the fare policy",
      "Told the caller a fee applies",
    ],
    tool: "cancel_pickup",
    good: "Read the free cancellation window, then cancelled with no fee",
    io: {
      in: "fare policy: (not read) · booked 1 min ago",
      out: "Cancelled with a $5 fee",
    },
  },
  "ISS-4431": {
    steps: [
      "Asked to send the order to a new address",
      "Called update_shipping_address",
      "The update failed; the agent didn't read the result",
      "Told the caller the address was changed",
    ],
    tool: "update_shipping_address",
    good: "Checked shipment_status first and said the parcel had left",
    io: {
      in: "update_shipping_address: error (parcel dispatched)",
      out: "Agent says the address was changed",
    },
  },
  "ISS-4552": {
    steps: [
      "Asked for a table for twelve",
      "Checked Saturday's availability",
      "Called create_booking without the large-party rule",
      "Confirmed the table to the guest",
    ],
    tool: "create_booking",
    good: "Took a number for the events team, as the rule says",
    io: {
      in: "party_size: 12 (events team rule: skipped)",
      out: "Table for twelve booked",
    },
  },
  "ISS-4557": {
    steps: [
      "Asked to move an appointment",
      "Found a patient by name",
      "Called reschedule_appointment before the date of birth was confirmed",
      "Told the caller the appointment was moved",
    ],
    tool: "reschedule_appointment",
    good: "Confirmed the name and date of birth, then rescheduled",
    io: {
      in: "patient: matched by name · date_of_birth: (not asked)",
      out: "Appointment moved to Tuesday 10am",
    },
  },
  "ISS-4548": { tool: "search_flights" },
  "ISS-4563": {
    steps: [
      "Asked for the loyalty discount",
      "Opened the order",
      "Called apply_discount_code without check_loyalty_status",
      "Told the caller the discount was applied",
    ],
    tool: "apply_discount_code",
    good: "Checked the loyalty tier, then applied the discount",
    io: {
      in: "loyalty tier: (not checked)",
      out: "15% loyalty discount applied",
    },
  },
  "ISS-4566": {
    tool: "generate_return_label",
    io: {
      in: "generate_return_label: error 502",
      out: "Agent says the label was sent",
    },
  },
  "ISS-4571": {
    steps: [
      "Asked what the group deposit is",
      "Called the booking tool",
      "Quoted the deposit from last season's price list",
      "Gave the caller the old deposit",
    ],
    tool: "create_booking",
    good: "Read the deposit from the booking result",
    io: {
      in: "deposit: $20 a head (last season)",
      out: "Quoted $280 for fourteen",
    },
  },
  "ISS-4574": {
    steps: [
      "Asked for the next flight, aisle seat",
      "Found a partner flight",
      "Rebooked without the seat request",
      "Told the traveller it's all set",
    ],
    tool: "rebook_flight",
    good: "Sent the seat request with the partner rebooking",
    io: {
      in: "segments: [partner 9:40] · seat: (dropped)",
      out: "Rebooked with no seat request",
    },
  },
  "ISS-4578": {
    steps: [
      "Asked to move to Thursday at 3",
      "Called the booking tool",
      "The slot was taken; the agent didn't read the result",
      "Told the caller the appointment was moved",
    ],
    tool: "reschedule_appointment",
    good: "Read the taken slot and offered the next free one",
    io: {
      in: "reschedule_appointment: slot taken",
      out: "Agent says Thursday 3pm is booked",
    },
  },
  "ISS-4582": {
    steps: [
      "Asked for a refund for a broken item",
      "Opened the order",
      "Offered store credit without checking the refund rule",
      "Told the caller store credit is the option",
    ],
    tool: "check_refund_policy",
    good: "Checked the order's refund rule, then refunded",
    io: { in: "refund rule: (not checked)", out: "Store credit offered" },
  },
  "ISS-4585": {
    steps: [
      "Asked about a late fee",
      "Fetched the invoice",
      "Sent a total without the late fee it just mentioned",
      "Gave the customer two different amounts",
    ],
    tool: "get_invoice",
    good: "Added the late fee, then quoted the total once",
    io: { in: "late fee: $35 · total: $1,275", out: "Agent says $1,240" },
  },
  "ISS-4593": {
    steps: [
      "Asked to move to the 6pm flight",
      "Called the rebooking tool",
      "Confirmed without reading the fare difference",
      "Told the traveller there was nothing to pay",
    ],
    tool: "rebook_flight",
    good: "Quoted the fare difference before confirming",
    io: {
      in: "fare difference: $86 (not read)",
      out: "Agent says nothing more to pay",
    },
  },
  "ISS-4589": {
    steps: [
      "Asked what cancelling would cost",
      "Opened the order",
      "Called cancel_order without asking",
      "Told the customer the order was cancelled",
    ],
    tool: "cancel_order",
    good: "Explained the policy and waited for a yes",
    io: { in: "intent: question (no yes given)", out: "Order cancelled" },
  },
  "ISS-4601": {
    steps: [
      "Asked for a table on Friday at 8",
      "Checked availability",
      "Booked without reading back the date and party size",
      "Told the guest they're all set",
    ],
    tool: "create_booking",
    good: "Read the date and party size back, then booked",
    io: {
      in: "date, party_size: (not read back)",
      out: "Table booked for the wrong day",
    },
  },
  "ISS-4602": {
    steps: [
      "Asked to pick up a repeat prescription",
      "Found a patient by name",
      "Booked the pickup without the identity check",
      "Told the caller it's ready on Thursday",
    ],
    tool: "book_prescription_pickup",
    good: "Verified the date of birth, then booked the pickup",
    io: {
      in: "date_of_birth: (not asked)",
      out: "Prescription pickup booked",
    },
  },
  "ISS-4603": {
    steps: [
      "Asked about the bag allowance after a rebooking",
      "Kept the original fare's details",
      "Quoted the allowance from the fare before the rebooking",
      "Told the traveller two bags are included",
    ],
    tool: "get_booking",
    good: "Read the allowance from the new booking",
    io: {
      in: "fare: basic (allowance from the old fare)",
      out: "Quoted two free bags",
    },
  },
  "ISS-4604": {
    steps: [
      "Said the card on file was cancelled",
      "Opened the order",
      "Refunded to the cancelled card",
      "Told the customer the refund is on its way",
    ],
    tool: "issue_refund",
    good: "Offered store credit or a new card, then refunded",
    io: {
      in: "refund_to: card ending 4417 (cancelled)",
      out: "Refund of $64 sent",
    },
  },
  "ISS-4605": {
    steps: [
      "Asked what closing an account involves",
      "Looked up the account",
      "Closed the account without asking",
      "Told the caller the account is closed",
    ],
    tool: "close_account",
    good: "Explained the steps and waited for a yes",
    io: { in: "intent: question (no yes given)", out: "Account closed" },
  },
  "ISS-4606": {
    steps: [
      "Asked for a car now",
      "Created the job",
      "Promised an ETA before a driver accepted",
      "Told the caller a driver is 5 minutes away",
    ],
    tool: "dispatch_job",
    good: "Said a driver is being found until one accepted",
    io: { in: "driver: (unassigned)", out: "Agent says 5 minutes away" },
  },
};

/* The issue's own story: its type's, with what its own traces show. */
const storyOf = (cluster) => {
  const base = STORIES[cluster?.error?.type] || STORIES.PolicyViolation;
  const own = ISSUE_STORIES[cluster?.cluster_id] || {};
  return { ...base, ...own, io: own.io || typeOf(cluster).io };
};

/** The failing and working evidence reels for one conversation of an issue. */
export const reelsFor = (cluster, input, agentSays) => {
  const t = typeOf(cluster);
  const st = storyOf(cluster);
  const io = st.io;
  const [ask, prep, wrong, told] = st.steps;
  const fail = [
    { label: "User", text: ask, note: `User said: "${input}"` },
    {
      label: "Agent",
      text: prep,
      wentIn: `${st.tool}: request received`,
      cameOut: "Context loaded",
    },
    {
      label: "Tool",
      text: wrong,
      note: `Where it goes wrong: ${t.label.toLowerCase()}.`,
      isFailure: true,
      wentIn: io.in,
      cameOut: io.out,
    },
    {
      label: "Agent",
      text: told,
      note: `Agent said: "${agentSays}"`,
      wentIn: "tool result",
      cameOut: io.out,
    },
  ];
  const pass = [
    { label: "User", text: ask, note: `User said: "${input}"` },
    {
      label: "Agent",
      text: prep,
      wentIn: `${st.tool}: request received`,
      cameOut: "Context loaded",
      status: "pass",
    },
    {
      label: "Tool",
      text: st.good,
      /* The value that was missing, now checked — or the whole input. */
      wentIn: /: \([^)]*\)$/.test(io.in)
        ? io.in.replace(/: \([^)]*\)$/, ": (checked)")
        : io.in.replace(/\s*\(.*?\)\s*$/, "").replace(/: .*$/, ": (checked)"),
      cameOut: "Result matches the request",
      status: "pass",
    },
    {
      label: "Agent",
      text: "Told the caller what actually happened",
      wentIn: "tool result",
      cameOut: "Reply matches the result",
      status: "pass",
    },
  ];
  return { fail, pass };
};
