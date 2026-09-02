/**
 * The sandbox guarantee.
 *
 * Every run happens against a shadow copy of the agent, never the production
 * one, and this is the screen that has to make that checkable rather than
 * asserted. A customer pointing a test harness at their own live system is the
 * failure mode that ends the trial, so the claim is spelled out as specific
 * mechanisms — what is copied, what is stubbed, what cannot leave.
 */

export const SHADOW_GUARANTEES = [
  {
    id: "shadow",
    label: "A shadow agent, not your production one",
    icon: "solar:copy-linear",
    note: "We stand up your agent from the source or bundle you pinned, inside our sandbox, with its own process and its own config. Your deployed agent is never called and never knows this ran.",
  },
  {
    id: "data",
    label: "Seeded data, not your data",
    icon: "solar:database-linear",
    note: "The world is built from the environment's seed and rebuilt for every task. No production database is read, connected to, or copied.",
  },
  {
    id: "credentials",
    label: "Test credentials only",
    icon: "solar:key-linear",
    note: "Secrets you supply in \"Needs your input\" are held for the length of a run and are never written into a scenario, a trace or an exportable file. We will not accept a production key.",
  },
  {
    id: "stubs",
    label: "Anything that would reach outside is stubbed",
    icon: "solar:plug-circle-linear",
    note: "Tools that mutate something beyond the sandbox replay a recorded response instead of firing. A scenario that ends in one is testing the decision, not the delivery.",
  },
  {
    id: "egress",
    label: "No egress from the instance",
    icon: "solar:shield-keyhole-linear",
    note: "Instances run without a route to the public internet except the endpoints the contract declares. Nothing the agent does inside can reach a real customer.",
  },
];

/** One line for a header chip, where there is no room for the list. */
export const SHADOW_SUMMARY =
  "Shadow agent in an isolated sandbox — seeded data, test credentials, no egress. Your production system is not involved.";
