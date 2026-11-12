// Shared journey fixtures have one writer: journeys-main-708.
// F09 is a fail-closed source-preparation contract, not a provisioned fixture.
export * as f09 from './f09/index.ts';
// F05 prepares money oracles; private projection/runtime admission is still UNBOUND.
export * as f05 from './f05/index.ts';
// F12 prepares TEST payment/consent oracles; private runtime bindings remain UNBOUND.
export * as f12 from './f12/index.ts';
