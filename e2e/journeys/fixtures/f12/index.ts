// Source preparation only. Importing does not allocate a signing key or perform I/O.
export { F12_CANDIDATE, F12_PROJECTION_REQUIREMENTS, paymentScope, testReference,
  requireF12Runtime, privatePaymentQuery, autoReloadBarrier } from './contract.ts';
export type { PaymentScope } from './contract.ts';
export { checkoutPlan, checkoutReturnPlan, cardPlan, settingsPlan } from './plans.ts';
export { createLocalSigner } from './signing.ts';
export { assertPaymentOutcome, assertDefaultCards } from './payments.ts';
export { consentQuery, assertConsentTransition, assertClaimRace } from './consent.ts';
