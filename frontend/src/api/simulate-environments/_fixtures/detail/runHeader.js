// Vel's run header samples (his prototype's proof kind, where a run was
// started from and the re-grade offer), copy without em dashes. Shown only
// with ?uiSample=proofKind, ?uiSample=provenance or ?uiSample=regrade: the API
// records none of them for a run yet.

export const SAMPLE_PROOF_KIND = {
  kind: "sealed",
  label: "Sealed",
  tierLabel: "Stateful world",
  note: "The agent ran in a sealed sandbox. Its tools answered from the world's data, so nothing reached a real system.",
};

export const SAMPLE_PROVENANCE = {
  label: "From CI · PR #482 · feat/refund-window @ 9c1e2ab",
  command: "fai sim run <environment> --repeats 3 --fail-under 0.8",
};

export const SAMPLE_REGRADE = {
  line: "2 sub-goals this run used got a grader fix since it ran. Re-grading reads the stored traces and end states again, with no new calls.",
  action: "Re-grade 12 scenarios",
};
