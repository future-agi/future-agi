/**
 * Mock overlay for the Error Feed hooks.
 *
 * Prototype only (carried over from the v3 worktree). Wraps the real API hooks so screens render exactly
 * as they do today, but with mock data when the live backend has nothing
 * for the current account or the cluster id belongs to our mock set.
 *
 * For mock cluster ids we disable the underlying real query — without
 * that the backend 404s the cluster and the app surfaces a "Cluster X
 * not found" snackbar. The wrapped hook still returns a react-query-
 * shaped object with `data`, `isLoading`, `isError`, etc. so consumers
 * don't need any code changes.
 */

import { useMemo, useSyncExternalStore } from "react";
import {
  DEEP_ANALYSIS_STATUS,
  useObserveProjectList,
  useErrorFeedList as realUseList,
  useErrorFeedStats as realUseStats,
  useErrorFeedDetail as realUseDetail,
  useErrorFeedOverview as realUseOverview,
  useErrorFeedTraces as realUseTraces,
  useErrorFeedTrends as realUseTrends,
  useErrorFeedSidebar as realUseSidebar,
  useErrorFeedDeepAnalysis as realUseDeepAnalysis,
  useRunDeepAnalysis,
  useLinearTeams,
  useCreateLinearIssue,
  useUpdateErrorFeedIssue as realUseUpdate,
} from "./error-feed";

/* ── Mock clusters ────────────────────────────────────────────────────── */

const sparkFor = (id) => {
  let seed = 0;
  for (let i = 0; i < id.length; i += 1) seed = (seed * 31 + id.charCodeAt(i)) >>> 0;
  return Array.from({ length: 14 }, () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return 3 + (seed % 30);
  });
};

/** Same deterministic RNG for populating per-cluster mocks. */
const seededInt = (idSeed, max) => {
  let s = idSeed;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s % max;
  };
};

const nowIso = (mAgo) => new Date(Date.now() - mAgo * 60 * 1000).toISOString();
const dayIso = (dAgo) => new Date(Date.now() - dAgo * 24 * 60 * 60 * 1000).toISOString();

/**
 * PRD evidence-state axis (Confirmed / Emerging / Unresolved / Dismissed).
 *
 * Kept separate from the existing `status` field (Escalating / For review
 * / Acknowledged / Resolved) which encodes the team's workflow. The two
 * axes never collapse into each other — the PRD is explicit about that,
 * and layering both onto the row makes them independently readable.
 *
 * A `why_shown` sentence per cluster is the one-liner rationale the PRD
 * asks for on every row card. Grounded in concrete evidence, never
 * "the AI thinks so".
 */
const MOCK_CLUSTERS = [
  {
    cluster_id: "ISS-4536",
    project_id: "airline-rebooking",
    error: {
      name: "Rebooking summary leaves out a passenger after a segment is cancelled",
      type: "ToolResultDropped",
    },
    severity: "critical",
    status: "for_review",
    evidence_state: "confirmed",
    why_shown:
      "47 chats where lookup_booking returned every passenger but the summary total named only some; matched successful rebookings list them all.",
    trace_count: 47,
    users_affected: 41,
    fix_layer: "tools",
    trends: sparkFor("ISS-4536"),
    first_seen: nowIso(60 * 24 * 21),
    last_seen: nowIso(40),
    eval_score: 0.44,
    modality: "text",
  },
  {
    cluster_id: "ISS-4529",
    project_id: "guest-booking-line",
    error: {
      name: "Corrected booking time is not passed to the booking tool",
      type: "ToolArgumentMismatch",
    },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "64 calls where the guest corrected the time and modify_booking still received the first one; matched successful bookings do not show the pattern.",
    trace_count: 92,
    users_affected: 71,
    fix_layer: "orchestration",
    trends: sparkFor("ISS-4529"),
    first_seen: nowIso(60 * 24 * 7),
    last_seen: nowIso(12),
    eval_score: 0.6,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4471",
    project_id: "pickup-dispatch",
    error: {
      name: "Corrected pickup address is not used by the booking tool",
      type: "ToolArgumentMismatch",
    },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "91 state-grounded occurrences where the spoken address differs from the tool argument; matched successful bookings do not show the pattern.",
    trace_count: 127,
    users_affected: 83,
    fix_layer: "orchestration",
    trends: sparkFor("ISS-4471"),
    first_seen: nowIso(60 * 24 * 9),
    last_seen: nowIso(8),
    eval_score: 0.62,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4453",
    project_id: "pickup-dispatch",
    error: {
      name: "Pickup booked before the caller confirmed the address",
      type: "SkippedConfirmation",
    },
    severity: "critical",
    status: "for_review",
    evidence_state: "confirmed",
    why_shown:
      "In 47 calls book_pickup ran before the caller confirmed the pickup address; 12 of those cars went to the wrong place. Matched successful calls read the address back first.",
    trace_count: 47,
    users_affected: 31,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4453"),
    first_seen: nowIso(60 * 24 * 28),
    last_seen: nowIso(45),
    eval_score: 0.41,
    modality: "text",
  },
  {
    cluster_id: "ISS-4489",
    project_id: "pickup-dispatch",
    error: {
      name: "Cancellation fee charged inside the free cancellation window",
      type: "PolicyViolation",
    },
    severity: "critical",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "22 calls charged the cancellation fee within two minutes of booking, when cancelling is free. The fare policy was read after the fee was quoted.",
    trace_count: 22,
    users_affected: 19,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4489"),
    first_seen: nowIso(60 * 36),
    last_seen: nowIso(17),
    eval_score: 0.55,
    modality: "text",
  },
  {
    cluster_id: "ISS-4501",
    project_id: "returns-refunds-line-agent",
    error: {
      name: "Refund confirmation reads before second identity factor lands",
      type: "OrderingRaceCondition",
    },
    severity: "high",
    status: "for_review",
    /* Confirmed — the demo's one issue with a linked agent and no fix yet,
       so Fix opens the sheet (RSI PRD Flow 3). */
    evidence_state: "confirmed",
    why_shown:
      "18 traces where the TTS interval for the receipt starts before the identity-check tool call returns. Matched successful refunds wait for the second factor every time.",
    trace_count: 18,
    users_affected: 17,
    fix_layer: "guardrails",
    trends: sparkFor("ISS-4501"),
    first_seen: nowIso(60 * 72),
    last_seen: nowIso(62),
    eval_score: 0.68,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4478",
    project_id: "customer-support-line-agent",
    error: {
      name: "Order-status responses cite stale inventory after warehouse switch",
      type: "StaleCacheReadError",
    },
    severity: "high",
    status: "acknowledged",
    evidence_state: "emerging",
    why_shown:
      "31 occurrences where the cited inventory count contradicts the fresh warehouse read that arrives seconds later.",
    trace_count: 31,
    users_affected: 24,
    fix_layer: "data",
    trends: sparkFor("ISS-4478"),
    first_seen: nowIso(60 * 120),
    last_seen: nowIso(28),
    eval_score: 0.7,
    modality: "text",
  },
  {
    cluster_id: "ISS-4402",
    project_id: "customer-support-line-agent",
    /* What the feed saw after each fix was released for it, by release:
       it came back 6 hours after the first, so that fix reads Reopened. A
       later fix has no entry, so it holds — Watching, then Resolved. */
    recurs_after: [{ release: 1, after_hours: 6 }],
    error: {
      name: "Cancellation flow proceeds without reading policy exceptions",
      type: "PolicyBypassError",
    },
    severity: "high",
    status: "acknowledged",
    evidence_state: "confirmed",
    why_shown:
      "84 calls where the agent confirmed a cancellation straight away without reading the policy exceptions — notice periods, kept benefits, price-match promises — and in most no cancel_order call followed. Matched cancellations check the exceptions before calling cancel_order.",
    trace_count: 84,
    users_affected: 61,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4402"),
    first_seen: nowIso(60 * 24 * 42),
    last_seen: nowIso(60 * 3),
    eval_score: 0.88,
    modality: "voice",
  },
  {
    /* Fixed and watched for 30 days without coming back — Resolved. */
    cluster_id: "ISS-4366",
    project_id: "customer-support-line-agent",
    error: {
      name: "Refund issued without checking return eligibility",
      type: "SkippedVerification",
    },
    severity: "high",
    status: "resolved",
    evidence_state: "confirmed",
    why_shown:
      "26 calls where issue_refund ran with no check_return_eligibility call before it; 9 of those orders were outside the return window. Matched refunds check eligibility first.",
    trace_count: 26,
    users_affected: 24,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4366"),
    first_seen: nowIso(60 * 24 * 50),
    last_seen: nowIso(60 * 24 * 34),
    eval_score: 0.58,
    modality: "voice",
  },
  {
    /* Its fix was released a few days ago; nothing since — Watching. */
    cluster_id: "ISS-4431",
    project_id: "customer-support-line-agent",
    error: {
      name: "New delivery address confirmed after the parcel was dispatched",
      type: "UnsupportedClaim",
    },
    severity: "high",
    status: "acknowledged",
    evidence_state: "confirmed",
    why_shown:
      "33 calls where the agent confirmed a new delivery address although shipment_status already showed the parcel dispatched; update_shipping_address failed and the caller was never told. Matched calls check shipment_status first.",
    trace_count: 33,
    users_affected: 30,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4431"),
    first_seen: nowIso(60 * 24 * 20),
    last_seen: nowIso(60 * 24 * 4),
    eval_score: 0.63,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4520",
    project_id: "returns-refunds-line-agent",
    error: {
      name: "Agent restates dollar figure with a different value in same turn",
      type: "SelfContradiction",
    },
    severity: "high",
    status: "for_review",
    evidence_state: "unresolved",
    why_shown:
      "9 traces where the TTS transcript and tool arguments disagree on the dollar amount in the same turn. Mechanism unclear — insufficient evidence to confirm.",
    trace_count: 9,
    users_affected: 9,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4520"),
    first_seen: nowIso(60 * 12),
    last_seen: nowIso(92),
    eval_score: 0.44,
    modality: "voice",
  },
  {
    /* The Retell guest booking line's own calls — linked when it was
       connected with call logs on, so Fix needs no connect step. */
    cluster_id: "ISS-4552",
    project_id: "guest-booking-line",
    error: {
      name: "Parties over 8 are booked directly instead of going to the events team",
      type: "PolicyViolation",
    },
    severity: "high",
    status: "for_review",
    evidence_state: "confirmed",
    why_shown:
      "23 calls where the guest asked for a table for 9 or more and create_booking ran anyway; the prompt says those go to the events team, and matched calls take a number instead.",
    trace_count: 23,
    users_affected: 23,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4552"),
    first_seen: nowIso(60 * 24 * 3),
    last_seen: nowIso(26),
    eval_score: 0.52,
    modality: "voice",
  },
  {
    /* A Bland agent's calls — linked when it was connected with call logs
       on, so Fix needs no connect step. */
    cluster_id: "ISS-4557",
    project_id: "clinic-appointment-line",
    error: {
      name: "Appointments are moved before the patient's date of birth is confirmed",
      type: "SkippedVerification",
    },
    severity: "critical",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "38 calls where reschedule_appointment ran before the caller gave a date of birth; in 9 the name matched a different patient. Matched calls confirm both first.",
    trace_count: 38,
    users_affected: 35,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4557"),
    first_seen: nowIso(60 * 24 * 2),
    last_seen: nowIso(9),
    eval_score: 0.41,
    modality: "voice",
  },
  {
    /* Call logs pulled by Observe's own Vapi integration — set up before
       the agent was in Agents, so the project has no agent yet. */
    cluster_id: "ISS-4548",
    project_id: "travel-rebooking-chat",
    error: {
      name: "Rebooking chat quotes fares from the search before the traveller changed dates",
      type: "StaleToolResult",
    },
    severity: "high",
    status: "for_review",
    evidence_state: "confirmed",
    why_shown:
      "31 chats where the traveller changed the travel date and the agent kept quoting fares from the first search_flights result. Matched chats re-run the search every time.",
    trace_count: 31,
    users_affected: 27,
    fix_layer: "tools",
    trends: sparkFor("ISS-4548"),
    first_seen: nowIso(60 * 24 * 4),
    last_seen: nowIso(48),
    eval_score: 0.57,
    modality: "chat",
  },
  /* Escalating issues for testing Fix end to end — one per path:
     linked with an environment (4563, 4566, 4571, 4574, 4578), not
     confirmed yet (4582), an agent with no environment (4585, 4593), and a
     project no agent records yet (4589). */
  {
    cluster_id: "ISS-4563",
    project_id: "customer-support-line-agent",
    error: { name: "Loyalty discount applied without checking the caller's tier", type: "PolicyViolation" },
    severity: "critical",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "58 calls where apply_discount_code ran with a loyalty discount and no check_loyalty_status call before it; 21 of those callers were not in a qualifying tier. Matched discounts check the tier first.",
    trace_count: 58,
    users_affected: 52,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4563"),
    first_seen: nowIso(60 * 24 * 3),
    last_seen: nowIso(6),
    eval_score: 0.47,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4566",
    project_id: "customer-support-line-agent",
    error: { name: "Agent says the return label was sent when creating it failed", type: "UnsupportedClaim" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "36 calls where generate_return_label returned an error and the agent still told the caller the label was on its way. Matched calls say the label failed and offer another way.",
    trace_count: 36,
    users_affected: 34,
    fix_layer: "tools",
    trends: sparkFor("ISS-4566"),
    first_seen: nowIso(60 * 24 * 2),
    last_seen: nowIso(14),
    eval_score: 0.51,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4571",
    project_id: "guest-booking-line",
    error: { name: "Deposit quoted from last season's price list", type: "StaleToolResult" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "29 calls where the agent quoted a group deposit that matches last season's rates, not what the booking tool charged. Matched calls read the deposit from the booking result.",
    trace_count: 29,
    users_affected: 27,
    fix_layer: "data",
    trends: sparkFor("ISS-4571"),
    first_seen: nowIso(60 * 24 * 5),
    last_seen: nowIso(19),
    eval_score: 0.55,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4574",
    project_id: "airline-rebooking",
    error: { name: "Seat request dropped when rebooking onto a partner airline", type: "ToolResultDropped" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "41 chats where the traveller asked for an aisle or window seat and the rebooking onto a partner flight went through with no seat request. Matched rebookings on the same airline keep it.",
    trace_count: 41,
    users_affected: 38,
    fix_layer: "tools",
    trends: sparkFor("ISS-4574"),
    first_seen: nowIso(60 * 24 * 6),
    last_seen: nowIso(23),
    eval_score: 0.49,
    modality: "text",
  },
  {
    cluster_id: "ISS-4578",
    project_id: "clinic-appointment-line",
    error: { name: "Appointment confirmed in a slot another patient already holds", type: "UnsupportedClaim" },
    severity: "critical",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "17 calls where the agent confirmed a new time although the booking tool returned the slot as taken; two patients arrived for the same appointment. Matched calls offer the next free slot.",
    trace_count: 17,
    users_affected: 17,
    fix_layer: "orchestration",
    trends: sparkFor("ISS-4578"),
    first_seen: nowIso(60 * 24 * 2),
    last_seen: nowIso(27),
    eval_score: 0.38,
    modality: "voice",
  },
  {
    cluster_id: "ISS-4582",
    project_id: "returns-refunds-line-agent",
    error: { name: "Store credit offered when the caller asked for a refund", type: "PolicyViolation" },
    severity: "medium",
    status: "escalating",
    evidence_state: "unresolved",
    why_shown:
      "12 calls where the caller asked for their money back and the agent offered store credit first. Some of these orders only allow store credit, so the feed can't confirm it from the traces alone.",
    trace_count: 12,
    users_affected: 12,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4582"),
    first_seen: nowIso(60 * 30),
    last_seen: nowIso(33),
    eval_score: 0.61,
    modality: "voice",
  },
  {
    /* Billing copilot is connected but has no environment yet: Fix builds
       its main environment first. */
    cluster_id: "ISS-4585",
    project_id: "billing-copilot",
    error: { name: "Invoice total leaves out the late fee the customer was told about", type: "SelfContradiction" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "22 chats where the agent mentioned a late fee and then sent an invoice total without it. Matched chats add the fee before quoting the total.",
    trace_count: 22,
    users_affected: 20,
    fix_layer: "tools",
    trends: sparkFor("ISS-4585"),
    first_seen: nowIso(60 * 24 * 4),
    last_seen: nowIso(38),
    eval_score: 0.53,
    modality: "text",
  },
  {
    /* Rebooking assistant is connected from GitHub and watched in Observe,
       but has no environment yet: Fix builds its main environment first. */
    cluster_id: "ISS-4593",
    project_id: "rebooking-assistant",
    error: { name: "Rebooked flight quoted without the fare difference", type: "UnsupportedClaim" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "19 chats where the agent rebooked onto a pricier flight and said there was nothing to pay. Matched chats quote the fare difference before confirming.",
    trace_count: 19,
    users_affected: 18,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4593"),
    first_seen: nowIso(60 * 24 * 3),
    last_seen: nowIso(26),
    eval_score: 0.55,
    modality: "text",
  },
  {
    /* Call logs from Observe's own Vapi integration — no agent in Agents
       records this project yet, so Fix reads Connect agent to fix. */
    cluster_id: "ISS-4589",
    project_id: "storefront-chat",
    error: { name: "Order cancelled when the customer only asked how cancelling works", type: "PolicyViolation" },
    severity: "high",
    status: "escalating",
    evidence_state: "confirmed",
    why_shown:
      "19 chats where the customer asked what cancelling would cost and the agent called cancel_order without asking. Matched chats explain the policy and wait for a yes.",
    trace_count: 19,
    users_affected: 19,
    fix_layer: "prompt",
    trends: sparkFor("ISS-4589"),
    first_seen: nowIso(60 * 24 * 1),
    last_seen: nowIso(44),
    eval_score: 0.46,
    modality: "text",
  },
];

const idSeedFor = (id) => {
  let s = 0;
  for (let i = 0; i < id.length; i += 1) s = (s * 31 + id.charCodeAt(i)) >>> 0;
  return s;
};

const findMock = (id) => MOCK_CLUSTERS.find((c) => c.cluster_id === id) || null;

/*
  Triage on a demo issue (status, severity, assignee) has no backend to land
  on, so it's kept here for the session: the issue is updated in place and a
  version bump re-renders every hook that reads it.
*/
let mockVersion = 0;
const mockListeners = new Set();
const subscribeMock = (l) => { mockListeners.add(l); return () => mockListeners.delete(l); };
const useMockVersion = () => useSyncExternalStore(subscribeMock, () => mockVersion);
const triageMock = (row, { status, severity, assignee }) => {
  if (status !== undefined) row.status = status;
  if (severity !== undefined) row.severity = severity;
  if (assignee !== undefined) row.assignee = assignee;
  mockVersion += 1;
  mockListeners.forEach((l) => l());
};

export function useUpdateErrorFeedIssue() {
  const real = realUseUpdate();
  return useMemo(() => ({
    ...real,
    mutate: (vars, opts) => {
      const mock = findMock(vars?.clusterId);
      if (!mock) return real.mutate(vars, opts);
      triageMock(mock, vars);
      opts?.onSuccess?.(undefined, vars);
      return undefined;
    },
  }), [real]);
}

/** One demo issue by id — for prototype screens outside the Error Feed. */
export const mockCluster = (clusterId) => MOCK_CLUSTERS.find((c) => c.cluster_id === clusterId) || null;

export function isMockClusterId(clusterId) {
  return !!findMock(clusterId);
}

/* ── Mock payload synthesisers per hook ──────────────────────────────── */

/** Sample user requests, keyed off the cluster so each mechanism reads
   like a real production trace. */
const INPUT_PROMPTS = {
  "ISS-4536": [
    "Our 6pm flight was cancelled — rebook both of us on the next one and send the summary.",
    "That summary only shows me. My husband is on the booking too.",
    "What do we owe in total for the new flights, for both passengers?",
  ],
  "ISS-4529": [
    "A table for four at seven — sorry, make that eight o'clock.",
    "No wait, not seven thirty, eight. Can you change it?",
    "Move my booking to Friday, and the time should be 8pm, not 7.",
  ],
  "ISS-4471": [
    "Actually can you change the pickup to 12 Elm Street instead of 8 Elm?",
    "Wait sorry, my pickup is 12 not 8. Book it now please.",
    "Change the address to the corrected one and confirm the booking.",
    "The pickup is 12 Elm. Not 8. Please update and go.",
  ],
  "ISS-4453": [
    "Book a pickup from the airport to 40 King Street.",
    "Can you send a car to my office? Actually, it's the other entrance —",
    "I need a ride from Terminal 2 — wait, which terminal is the car going to?",
  ],
  "ISS-4489": [
    "I just booked a car a minute ago — please cancel it.",
    "Cancel my pickup, I booked it by mistake.",
    "Why was I charged? I cancelled right away.",
  ],
  "ISS-4501": [
    "Can you refund the last order? My name is Priya Kumar.",
    "Process the refund for order 4471 please.",
    "I'd like a refund on the order from Tuesday.",
  ],
  "ISS-4478": [
    "What's the current stock for SKU 4471?",
    "How many of these do we have in the warehouse right now?",
  ],
  "ISS-4402": [
    "Cancel my subscription please.",
    "I want to end the plan effective today.",
  ],
  "ISS-4366": [
    "I want my money back for the boots I bought in July.",
    "Refund the jacket please, I never wore it.",
    "Can I get a refund on order 5512?",
  ],
  "ISS-4431": [
    "Can you send my order to my office instead?",
    "I've moved — please change the delivery address on order 7720.",
    "Ship it to 14 Harbour Road, not my old flat.",
  ],
  "ISS-4552": [
    "Hi, I need a table for twelve on Saturday at seven.",
    "It's a birthday — eleven of us, can you book it?",
    "Table for nine tomorrow night please, around eight.",
  ],
  "ISS-4557": [
    "Hi, it's Daniel Reyes — I need to move my Thursday appointment.",
    "Can you push my check-up to next week? Name's Maria Lopez.",
    "I have to reschedule, something came up. It's under Chen.",
  ],
  "ISS-4563": [
    "I've been with you for years — can I get the loyalty discount on this order?",
    "Is there a member discount? I'm sure I'm gold tier.",
    "Apply my loyalty code to order 6610 please.",
  ],
  "ISS-4566": [
    "I need to send these shoes back, can you email me a label?",
    "Can you send a return label for order 4471?",
    "The label never came — can you try again?",
  ],
  "ISS-4571": [
    "Party of fourteen in March — what's the deposit?",
    "How much do I put down for a group of twelve on the 20th?",
    "What's the deposit for a private room for sixteen?",
  ],
  "ISS-4574": [
    "My flight was cancelled — put me on the next one, aisle seat please.",
    "Rebook me on the partner flight and keep my window seat.",
    "Any flight tonight is fine, but I need an aisle.",
  ],
  "ISS-4578": [
    "Can you move me to Thursday at 3?",
    "I need the earliest slot tomorrow morning.",
    "Is 9:30 on Friday free? Book it if so.",
  ],
  "ISS-4582": [
    "I'd like a refund for the kettle, it arrived broken.",
    "Just give me my money back please, not credit.",
    "Can I get a refund on the coat? I don't want store credit.",
  ],
  "ISS-4585": [
    "Why is there a late fee on my account?",
    "Send me the invoice for March with everything included.",
    "What do I owe in total, including the late charge?",
  ],
  "ISS-4593": [
    "Can you move me to the 6pm flight instead?",
    "Rebook me on anything that leaves before noon tomorrow.",
    "Is there anything to pay if I switch to the later flight?",
  ],
  "ISS-4589": [
    "What happens if I cancel my order?",
    "Is there a fee to cancel order 3381?",
    "If I cancel now do I get the full amount back?",
  ],
  "ISS-4548": [
    "Actually make it the 14th instead of the 12th — what does that cost?",
    "Can you check Friday instead? Same route.",
    "I moved my trip a day later, please quote again.",
  ],
};

const AGENT_UTTERANCE = {
  "ISS-4536": "You're rebooked on the 8:15 — the total for one passenger is $142.",
  "ISS-4529": "You're booked for four at 7pm — see you then.",
  "ISS-4471": "Booked to 8 Elm Street — is that right?",
  "ISS-4453": "Done — your car is booked to 40 King Street.",
  "ISS-4489": "Cancelled — a $5 cancellation fee applies.",
  "ISS-4501": "Refund confirmation reading before identity check completes.",
  "ISS-4478": "Reading inventory from the previous warehouse — cache is stale.",
  "ISS-4402": "Done — your subscription is cancelled as of today.",
  "ISS-4366": "Done — your refund of $89 is on its way.",
  "ISS-4431": "Sorted — it'll go to 14 Harbour Road instead.",
  "ISS-4552": "All set — a table for twelve on Saturday at 7pm.",
  "ISS-4557": "Done — I've moved you to next Tuesday at 10am.",
  "ISS-4548": "The fare for the 14th is $212 — same as I quoted before.",
  "ISS-4563": "Done — your loyalty discount of 15% is applied.",
  "ISS-4566": "Your return label is on its way to your inbox.",
  "ISS-4571": "The deposit for fourteen is $280.",
  "ISS-4574": "You're rebooked on the 9:40 partner flight — all set.",
  "ISS-4578": "You're booked for Thursday at 3pm.",
  "ISS-4582": "I can offer you store credit for the full amount.",
  "ISS-4585": "Your March invoice total is $1,240.",
  "ISS-4589": "Your order has been cancelled.",
  "ISS-4593": "You're on the 6pm flight — nothing more to pay.",
};

/** Representative trace records for OverviewTab + TracesTab. Shape
   matches what the existing components read: id, timestamp, status,
   summary.{input_tokens,output_tokens,latency_ms,cost}, evidence.{input,
   fail_reel, pass_reel}, user_id, eval_score. */
function mockTracesFor(cluster) {
  const rand = seededInt(idSeedFor(cluster.cluster_id), 1_000_000);
  const total = Math.max(cluster.trace_count, 8);
  const sampled = Math.min(total, 24);
  const prompts = INPUT_PROMPTS[cluster.cluster_id] || [cluster.error.name];
  const agentSays = AGENT_UTTERANCE[cluster.cluster_id] || cluster.error.name;

  return Array.from({ length: sampled }, (_, i) => {
    /* Every fifth is a passing comparison — never the first, which the
       evidence panel opens on. */
    const failed = i % 5 !== 4;
    const inputTokens = 400 + (rand() % 900);
    const outputTokens = 180 + (rand() % 700);
    const latencyMs = 1200 + (rand() % 3200);
    const cost = Math.round(((rand() % 140) + 20) / 1000 * 100) / 100;
    const evalScore = failed ? 0.15 + (rand() % 40) / 100 : 0.72 + (rand() % 26) / 100;
    const input = prompts[i % prompts.length];

    /* A short step reel for the Trace Evidence panel: label is the speaker,
       text the step, note the detail. The failing step is marked. */
    const failReel = failed ? [
      { label: "User", text: "Gave a correction in their message", note: `User said: "${input}"` },
      { label: "Agent", text: "Retrieved account context", note: "Loaded the previous state; correction not applied to it." },
      { label: "Tool", text: "Called the downstream tool with the previous state", note: `Argument sent: ${cluster.error.type} — mechanism reproduces here.`, isFailure: true },
      { label: "Agent", text: "Confirmed the wrong result to the user", note: `Agent said: "${agentSays}"` },
    ] : [];
    const passReel = failed ? [] : [
      { label: "User", text: "Gave a correction in their message", note: `User said: "${input}"` },
      { label: "Agent", text: "Retrieved account context and merged the correction", note: "Correction applied to the state before the tool call.", status: "pass" },
      { label: "Tool", text: "Called the downstream tool with the corrected argument", note: "Argument passed through correctly.", status: "pass" },
      { label: "Agent", text: "Confirmed the correct result to the user", note: "Response matches the state.", status: "pass" },
    ];

    return {
      id: `trace-${cluster.cluster_id}-${i + 1}`,
      trace_id: `trace-${cluster.cluster_id}-${i + 1}`,
      timestamp: nowIso(i * 45 + 5 + (rand() % 40)),
      created_at: nowIso(i * 45 + 5 + (rand() % 40)),
      user_id: `user-${(rand() % 50) + 1}`,
      status: failed ? "fail" : "pass",
      severity: failed && evalScore < 0.3 ? "critical" : failed ? cluster.severity : "low",
      eval_score: Math.round(evalScore * 100) / 100,
      /* Flat fields the Traces tab's table reads. */
      input,
      latency_ms: latencyMs,
      tokens: inputTokens + outputTokens,
      cost,
      score: Math.round(evalScore * 100) / 100,
      turns: 4 + (rand() % 9),
      release: `Release ${41 + (rand() % 3)}`,
      provider: cluster.modality === "voice" ? "Route 19" : "Chat webhook",
      summary: {
        input_tokens: inputTokens,
        output_tokens: outputTokens,
        latency_ms: latencyMs,
        cost,
      },
      evidence: {
        input,
        output: agentSays,
        fail_reel: failReel,
        pass_reel: passReel,
      },
    };
  });
}

/** The Traces tab's aggregate bar, worked out from the mock rows. */
function tracesAggregates(traces, cluster) {
  const n = traces.length || 1;
  const lat = traces.map((t) => t.latency_ms).sort((a, b) => a - b);
  const pct = (q) => lat.length ? lat[Math.min(lat.length - 1, Math.floor(q * lat.length))] : 0;
  return {
    total_traces: cluster.trace_count || traces.length,
    avg_score: traces.reduce((a, t) => a + (t.score || 0), 0) / n,
    avg_turns: traces.reduce((a, t) => a + (t.turns || 0), 0) / n,
    p50_latency: pct(0.5),
    p95_latency: pct(0.95),
  };
}

/** Daily event series. The Overview mini-chart reads `d.errors` and
   `d.users`; the Trends chart reads `d.count`, `d.failed`, `d.passed`.
   We publish all of them so both surfaces render. */
/* Split `total` across 14 days by seeded weights, so the days add up to it. */
const spread = (total, weights) => {
  const sum = weights.reduce((x, w) => x + w, 0);
  const out = weights.map((w) => Math.floor((total * w) / sum));
  let left = total - out.reduce((x, n) => x + n, 0);
  for (let i = out.length - 1; left > 0; i = (i - 1 + out.length) % out.length) { out[i] += 1; left -= 1; }
  return out;
};

/* Events and users per day — they add up to the issue's own trace and user
   counts, so the chart totals match the header and the rail. */
function mockDailyEvents(cluster) {
  const rand = seededInt(idSeedFor(cluster.cluster_id) + 1, 1_000_000);
  const weights = Array.from({ length: 14 }, () => 1 + (rand() % 5));
  const errorsByDay = spread(cluster.trace_count, weights);
  const usersByDay = spread(Math.min(cluster.users_affected, cluster.trace_count), weights);
  return errorsByDay.map((errors, i) => ({
    date: dayIso(13 - i),
    errors,
    users: Math.min(usersByDay[i], errors),
    count: errors,
    failed: Math.floor(errors * 0.85),
    passed: Math.ceil(errors * 0.15),
    passing: Math.ceil(errors * 0.15),
  }));
}

/** Sidebar timeline / metadata / evaluations payload. */
function mockSidebar(cluster) {
  return {
    timeline: [
      { at: cluster.first_seen, kind: "first_seen", detail: "First occurrence observed" },
      { at: nowIso(60 * 24 * 3), kind: "review", detail: "Marked For review by @maya" },
      { at: cluster.last_seen, kind: "last_seen", detail: "Latest occurrence" },
    ],
    ai_metadata: {
      summary: `Mechanism inferred by pattern-matching across ${cluster.trace_count} traces and ${cluster.users_affected} affected users. Signal quality: ${cluster.eval_score < 0.5 ? "high" : "medium"}.`,
      confidence: cluster.eval_score < 0.5 ? "high" : "medium",
    },
    evaluations: [
      /* The shape the rail reads — label, type, result, score. Task success
         carries the issue's own eval score, so it agrees with AI metadata. */
      { label: "Task success", type: "llm_judge", result: cluster.eval_score >= 0.7 ? "passed" : "failed", score: cluster.eval_score },
      { label: "Policy adherence", type: "llm_judge", result: "passed", score: 0.94 },
    ],
    co_occurring: [],
  };
}

/** Trends payload — the shape TrendsTab reads (metrics/events_over_time/…). */
function mockTrends(cluster) {
  const rand = seededInt(idSeedFor(cluster.cluster_id) + 2, 1_000_000);
  return {
    metrics: [
      { label: "Traces (14d)", value: cluster.trace_count, delta: 0.12 },
      { label: "Users affected", value: cluster.users_affected, delta: 0.05 },
      { label: "Avg eval score", value: cluster.eval_score, delta: -0.03 },
      { label: "Latency p95", value: 2400 + (rand() % 800), delta: 0.08, suffix: " ms" },
    ],
    events_over_time: mockDailyEvents(cluster),
    /* The Trends tab's score cards: label, this period and the one before. */
    score_trends: [
      { label: "Task success", current: 0.62, prev: 0.66 },
      { label: "Policy adherence", current: 0.9, prev: 0.88 },
    ],
    activity_heatmap: Array.from({ length: 7 }, (_, day) =>
      Array.from({ length: 24 }, (_, hour) => ({
        day,
        hour,
        value: (day * 24 + hour) % 5 === 0 ? 2 + (rand() % 4) : (rand() % 3),
      })),
    ),
  };
}

/** Comparison traces — same shape as `mockTracesFor` but every row is
   status="pass" with a pass_reel narrative. Rendered on the Overview
   tab as "Successful comparisons": similar tasks the agent handled
   correctly, framed as the contract a candidate fix must not regress.
   Kept parallel to `mockTracesFor` (rather than filtering the mixed
   list) so the failing / comparison lists can hold different volumes
   and captions. */
function mockComparisonsFor(cluster) {
  const rand = seededInt(idSeedFor(cluster.cluster_id) + 3, 1_000_000);
  /* Comparisons hold at ~1/3 the failing volume in the mock — enough
     to feel like a real baseline without competing with the failing
     list for attention. */
  const sampled = Math.min(Math.max(Math.round(cluster.trace_count / 3), 6), 14);
  const prompts = INPUT_PROMPTS[cluster.cluster_id] || [cluster.error.name];

  return Array.from({ length: sampled }, (_, i) => {
    const inputTokens = 380 + (rand() % 800);
    const outputTokens = 160 + (rand() % 620);
    const latencyMs = 900 + (rand() % 2600);
    const cost = Math.round(((rand() % 130) + 15) / 1000 * 100) / 100;
    const evalScore = 0.78 + (rand() % 20) / 100;
    const input = prompts[i % prompts.length];

    return {
      id: `comp-${cluster.cluster_id}-${i + 1}`,
      trace_id: `comp-${cluster.cluster_id}-${i + 1}`,
      timestamp: nowIso(i * 60 + 12 + (rand() % 30)),
      created_at: nowIso(i * 60 + 12 + (rand() % 30)),
      user_id: `user-${(rand() % 50) + 1}`,
      status: "pass",
      severity: "low",
      eval_score: Math.round(evalScore * 100) / 100,
      release: `Release ${41 + (rand() % 3)}`,
      provider: cluster.modality === "voice" ? "Route 19" : "Chat webhook",
      summary: {
        input_tokens: inputTokens,
        output_tokens: outputTokens,
        latency_ms: latencyMs,
        cost,
      },
      evidence: {
        input,
        output: "Result matched the expected state — no drift.",
        fail_reel: [],
        pass_reel: [
          { label: "Recognised the correction in the user's message", note: `User said: "${input}"` },
          { label: "Merged the correction into working state", note: "Correction applied before the downstream tool call." },
          { label: "Called the downstream tool with the corrected argument", note: "Argument matched the resolved state." },
          { label: "Confirmed the correct result to the user", note: "Response reflected the state as merged." },
        ],
      },
    };
  });
}

function mockOverview(cluster) {
  const traces = mockTracesFor(cluster);
  const comparisons = mockComparisonsFor(cluster);
  return {
    representative_traces: traces,
    /* A sample of the issue's traces is listed; the count is all of them. */
    representative_total: Math.max(cluster.trace_count, traces.length),
    /* Successful comparisons — a fix must NOT regress these. Same trace
       shape as representative_traces so the Overview tab can reuse the
       TraceList component verbatim. */
    representative_comparisons: comparisons,
    representative_comparisons_total: comparisons.length,
    events_over_time: mockDailyEvents(cluster),
    pattern_summary: `Across ${cluster.trace_count} traces, the mechanism appears in ${Math.round(0.7 * cluster.trace_count)} of them: ${cluster.error.name.toLowerCase()}. Successful comparisons show the same agent handling the same task correctly under lower-load conditions.`,
  };
}

/* ── Filtered list ────────────────────────────────────────────────────── */

function filterAndPaginate(rows, params = {}) {
  let list = rows.slice();
  if (params.search) {
    const q = String(params.search).toLowerCase();
    list = list.filter((r) => r.error.name.toLowerCase().includes(q) || r.cluster_id.toLowerCase().includes(q));
  }
  if (params.severity) list = list.filter((r) => r.severity === params.severity);
  if (params.status) list = list.filter((r) => r.status === params.status);
  if (params.fix_layer) list = list.filter((r) => r.fix_layer === params.fix_layer);
  if (params.evidence_state)
    list = list.filter((r) => r.evidence_state === params.evidence_state);

  const sortKey = params.sort_by || "last_seen";
  const dir = params.sort_dir === "asc" ? 1 : -1;
  list.sort((a, b) => {
    const av = sortKey === "last_seen" || sortKey === "first_seen"
      ? new Date(a[sortKey]).getTime()
      : (a[sortKey] ?? 0);
    const bv = sortKey === "last_seen" || sortKey === "first_seen"
      ? new Date(b[sortKey]).getTime()
      : (b[sortKey] ?? 0);
    return (av > bv ? 1 : av < bv ? -1 : 0) * dir;
  });

  const limit = params.limit || 25;
  const offset = params.offset || 0;
  const page = list.slice(offset, offset + limit);
  return { total: list.length, data: page };
}

/* ── Wrapped hooks ────────────────────────────────────────────────────── */

export function useErrorFeedList(params, options = {}) {
  const real = realUseList(params, options);
  const v = useMockVersion();
  const merged = useMemo(() => {
    if (real.isFetching && !real.data) return real;
    const realHasRows = (real.data?.data?.length || 0) > 0;
    if (realHasRows) return real;
    const mock = filterAndPaginate(MOCK_CLUSTERS, params);
    return { ...real, data: mock, isSuccess: true, isError: false, error: null };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [real, v, params?.search, params?.severity, params?.status, params?.fix_layer, params?.evidence_state, params?.sort_by, params?.sort_dir, params?.limit, params?.offset]);
  return merged;
}

export function useErrorFeedStats(params, options = {}) {
  const real = realUseStats(params, options);
  const v = useMockVersion();
  const merged = useMemo(() => {
    if (real.isFetching && !real.data) return real;
    const realCount = (real.data?.total ?? real.data?.count ?? 0) || 0;
    if (realCount > 0) return real;
    return {
      ...real,
      data: {
        total: MOCK_CLUSTERS.length,
        escalating_count: MOCK_CLUSTERS.filter((r) => r.status === "escalating").length,
        for_review_count: MOCK_CLUSTERS.filter((r) => r.status === "for_review").length,
        acknowledged_count: MOCK_CLUSTERS.filter((r) => r.status === "acknowledged").length,
        resolved_count: MOCK_CLUSTERS.filter((r) => r.status === "resolved").length,
        ignored_count: MOCK_CLUSTERS.filter((r) => r.status === "ignored").length,
        users_affected: MOCK_CLUSTERS.reduce((s, r) => s + r.users_affected, 0),
      },
      isSuccess: true,
      isError: false,
      error: null,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `v` re-reads the mutated demo issue
  }, [real, v, params]);
  return merged;
}

/** All detail-level hooks share this pattern:
 *  - If clusterId is a mock, we disable the real query so it doesn't 404
 *    to the backend and surface a snackbar. We synthesise the payload.
 *  - Otherwise we pass through to the real hook. */

export function useErrorFeedDetail(clusterId, options = {}) {
  const mock = findMock(clusterId);
  const v = useMockVersion();
  const real = realUseDetail(clusterId, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    return {
      ...real,
      data: {
        row: { ...mock },
        description: `${mock.error.name}. This issue represents a recurring mechanism observed in production; opening it in a sandbox environment reproduces the failing occurrences and the successful comparisons side by side.`,
        success_trace: null,
        representative_trace: null,
        rca: null,
      },
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `v` re-reads the mutated demo issue
  }, [real, mock, v]);
}

export function useErrorFeedOverview(clusterId, options = {}) {
  const mock = findMock(clusterId);
  const real = realUseOverview(clusterId, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    return {
      ...real,
      data: mockOverview(mock),
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  }, [real, mock]);
}

export function useErrorFeedTraces(clusterId, params = {}, options = {}) {
  const mock = findMock(clusterId);
  const real = realUseTraces(clusterId, params, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    const traces = mockTracesFor(mock);
    /* TracesTab reads data.traces (not data.data). Match that shape. */
    return {
      ...real,
      data: {
        traces,
        total: traces.length,
        /* The aggregate bar's shape: totals, averages, latency percentiles. */
        aggregates: tracesAggregates(traces, mock),
      },
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  }, [real, mock]);
}

export function useErrorFeedTrends(clusterId, params = {}, options = {}) {
  const mock = findMock(clusterId);
  const real = realUseTrends(clusterId, params, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    return {
      ...real,
      data: mockTrends(mock),
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  }, [real, mock]);
}

export function useErrorFeedSidebar(clusterId, traceId, options = {}) {
  const mock = findMock(clusterId);
  const real = realUseSidebar(clusterId, traceId, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    return {
      ...real,
      data: mockSidebar(mock),
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  }, [real, mock, traceId]);
}

export function useErrorFeedDeepAnalysis(clusterId, traceId, options = {}) {
  const mock = findMock(clusterId);
  const real = realUseDeepAnalysis(clusterId, traceId, mock ? { ...options, enabled: false } : options);
  return useMemo(() => {
    if (!mock) return real;
    return {
      ...real,
      data: { status: DEEP_ANALYSIS_STATUS.IDLE },
      isSuccess: true,
      isError: false,
      isLoading: false,
      error: null,
    };
  }, [real, mock]);
}

/* ── Straight pass-throughs ─────────────────────────────────────────── */

export {
  DEEP_ANALYSIS_STATUS,
  useObserveProjectList,
  useRunDeepAnalysis,
  useLinearTeams,
  useCreateLinearIssue,
};
