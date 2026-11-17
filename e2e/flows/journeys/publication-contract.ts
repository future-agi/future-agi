// Journey adoption gate, not a replacement harness. There is deliberately no
// environment override: a filename or E2E_VALUE_FREE=1 cannot establish review
// acceptance. The harness owner must supply an accepted publication boundary;
// its integration and proof replace this gate in a separate 713 ledger row.
// Latest independent record: HR-JRN-01-R4-REVIEW-1028.json, verify.state=blocked
// at 0a4202724e030910271901a426b30d0f3334c598 (three publication findings).
export const JOURNEY_PUBLICATION_ACCEPTED: boolean = false;
export const JOURNEY_PUBLICATION_GAP =
  'HARNESS-GAP HR-JRN-01: no accepted value-free ScopeActors/report publication path; refuse before provisioning, including W00 dependencies; raw private reports plus sanitized pulls do not satisfy this contract';

export function requireJourneyPublication(): void {
  if (!JOURNEY_PUBLICATION_ACCEPTED) throw new Error(JOURNEY_PUBLICATION_GAP);
  if (process.env.E2E_VALUE_FREE !== '1') throw new Error('JOURNEY_VALUE_FREE_REQUIRED');
}
