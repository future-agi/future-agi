/* How each kind of agent's candidate is submitted and tested. */
export const CANDIDATE_ROUTES = [
  {
    source: "Code in a repository",
    submits: "A commit on a branch, or its draft PR",
    tested: "Built into a sealed snapshot, run next to today's version",
  },
  {
    source: "Uploaded code",
    submits: "An archive of the patched code",
    tested: "Stored as a snapshot, run next to today's version",
  },
  {
    source: "Vapi, Retell and other hosted platforms",
    submits: "A settings patch, through submit_candidate",
    tested:
      "Applied to a test copy or draft version in your account, never the live assistant",
  },
  {
    source: "Built in Future AGI",
    submits: "A draft prompt or agent version",
    tested: "Saved as a draft, never active, run next to the active version",
  },
  {
    source: "Local code through our SDK",
    submits: "The commit it's on, plus a fingerprint of uncommitted changes",
    tested:
      "Our SDK runs it on your machine against the same scenarios; we score it. The proof says Run on your machine",
  },
  {
    source: "Endpoint or phone number",
    submits: "The address or version it deployed",
    tested: "Our scenarios against it. The proof says Deployed by you",
  },
];
