/*
  The Knowledge base, as the environment's Sources panel sees it: collections
  of files, each with revisions, and what changed between them. A source that
  came from here is a copy of the revision picked — pinned, never live — and
  a newer revision only ever arrives as a refresh to review.

  Nothing here reads a real Knowledge base; the files and their revisions are
  fixed so the same screens read the same way each time.
*/

/* Passage and row changes between a file's revisions. `match` finds the
   scenarios a change touches by their text. */
const POLICY_REV3 = [
  {
    id: "c1",
    kind: "rule-changed",
    label: "Return window",
    ref: "p.1",
    from: "Items can be returned within 30 days of delivery for a full refund.",
    to: "Items can be returned within 14 days of delivery for a full refund.",
    fromRule: "Refunds are allowed within 30 days of delivery.",
    toRule: "Refunds are allowed within 14 days of delivery.",
    passage: {
      id: "p1",
      page: 1,
      text: "Items can be returned within 14 days of delivery for a full refund.",
      rule: "Refunds are allowed within 14 days of delivery.",
    },
    match: /return|window|\bday\b|\d+ days|expire|late/i,
  },
  {
    id: "c2",
    kind: "rule-changed",
    label: "Supervisor approval amount",
    ref: "p.3",
    from: "Refunds over $500 need a supervisor's approval before they are issued.",
    to: "Refunds over $300 need a supervisor's approval before they are issued.",
    fromRule: "Refunds over $500 wait for a supervisor's approval.",
    toRule: "Refunds over $300 wait for a supervisor's approval.",
    passage: {
      id: "p5",
      page: 3,
      text: "Refunds over $300 need a supervisor's approval before they are issued.",
      rule: "Refunds over $300 wait for a supervisor's approval.",
      approval: { above: 300, by: "Supervisor" },
    },
    match: /approv|supervisor|large|\$\d{3}|over/i,
  },
  {
    id: "c3",
    kind: "rule-added",
    label: "Gift-card purchases",
    ref: "p.3",
    to: "Gift-card purchases are refunded as store credit, never to the card.",
    toRule: "Gift-card purchases are refunded as store credit only.",
    passage: {
      id: "p6",
      page: 3,
      text: "Gift-card purchases are refunded as store credit, never to the card.",
      rule: "Gift-card purchases are refunded as store credit only.",
    },
    match: /gift|credit|store credit|voucher/i,
  },
];

const ORDERS_REV7 = [
  {
    id: "c1",
    kind: "row-added",
    label: "40 orders",
    detail: "O-501 to O-540",
    count: 40,
    ids: ["O-501", "O-540"],
    /* Two of the new orders belong to buyers the customers file doesn't have. */
    badRows: [
      {
        row: 511,
        reason: "buyer_id C-1204 isn't in customers",
        table: "customers",
      },
      {
        row: 538,
        reason: "buyer_id C-1311 isn't in customers",
        table: "customers",
      },
    ],
    match: /order|refund|return|delivered|late/i,
  },
  {
    id: "c2",
    kind: "row-changed",
    label: "3 orders",
    detail: "O-104, O-118, O-230 — delivery date and price fixed",
    count: 3,
    ids: ["O-104", "O-118", "O-230"],
    match: /O-104|O-118|O-230|duplicate|retry|delivered/i,
  },
  {
    id: "c3",
    kind: "row-removed",
    label: "2 orders",
    detail: "O-377, O-412 — cancelled before dispatch",
    count: 2,
    ids: ["O-377", "O-412"],
    match: /O-377|O-412|cancel/i,
  },
];

/* Workspace files someone else owns — added by reference, never copied. */
export const ORG_SOURCES = [
  {
    id: "org-refund-policy",
    name: "Refund policy 2026.pdf",
    owner: "Legal",
    collection: "Policies",
    revision: 3,
    access: true,
    role: "policy",
    revisions: [
      { rev: 1, at: "2026-01-08", note: "First published", days: 30 },
      {
        rev: 2,
        at: "2026-04-14",
        note: "Added the supervisor approval step",
        days: 30,
      },
      {
        rev: 3,
        at: "2026-10-27",
        note: "Return window shortened to 14 days",
        days: 14,
      },
    ],
    changes: { 3: POLICY_REV3 },
  },
  {
    id: "org-orders-q3",
    name: "orders-q3-sample.csv",
    owner: "Data team",
    collection: "Order exports",
    revision: 7,
    access: true,
    role: "seed",
    revisions: [
      { rev: 5, at: "2026-09-02", note: "September export" },
      { rev: 6, at: "2026-09-30", note: "End-of-quarter export" },
      { rev: 7, at: "2026-10-29", note: "Late orders and corrections added" },
    ],
    changes: { 7: ORDERS_REV7 },
  },
  {
    id: "org-macros",
    name: "Support macros.docx",
    owner: "Support team",
    collection: "Support",
    revision: 4,
    access: true,
    role: "reference",
    revisions: [
      { rev: 3, at: "2026-06-11", note: "Tone guide refreshed" },
      { rev: 4, at: "2026-09-19", note: "Escalation macros added" },
    ],
    changes: {},
  },
  {
    id: "org-payroll",
    name: "HR payroll 2026.xlsx",
    owner: "People team",
    collection: "People",
    revision: 2,
    access: false,
    role: "evaluation-reference",
    revisions: [
      { rev: 1, at: "2026-02-03", note: "First published" },
      { rev: 2, at: "2026-08-21", note: "Q3 changes" },
    ],
    changes: {},
  },
];

export const kbFileOf = (fileId) => ORG_SOURCES.find((o) => o.id === fileId);

/** Files grouped by collection, in the order the Knowledge base lists them. */
export const kbCollections = () => {
  const out = [];
  ORG_SOURCES.forEach((o) => {
    let c = out.find((x) => x.name === o.collection);
    if (!c) {
      c = { name: o.collection, owner: o.owner, files: [] };
      out.push(c);
    }
    c.files.push(o);
  });
  return out;
};

/** A source copied from a Knowledge base file is pinned to this revision. */
export const kbRevisionLabel = (rev) => `rev ${rev}`;

/**
 * Is a newer revision waiting for this source? Null while the pinned one is
 * the latest, and while access is revoked — a refresh is blocked then, and
 * the pinned snapshot stays what the environment uses.
 */
export const kbRefreshOf = (source) => {
  if (!source?.kb?.fileId || source.revoked || source.archived) return null;
  const file = kbFileOf(source.kb.fileId);
  if (!file || file.revision <= source.kb.revision) return null;
  return {
    from: source.kb.revision,
    latest: file.revision,
    note: file.revisions.find((r) => r.rev === file.revision)?.note || "",
  };
};

/** The changes in one revision of a file. */
export const kbChangesOf = (fileId, rev) =>
  kbFileOf(fileId)?.changes?.[rev] || [];
