import { describe, expect, it } from 'vitest';
import { productionBaseline } from './production-baseline';

const runtime = (selector, extra = {}) => ({ kind: 'runtime', retiredAt: null, selector, ...extra });

describe('productionBaseline', () => {
  it('prefers the production label', () => {
    expect(productionBaseline([{ name: 'production', version: 5 }], [runtime({ version: 6 })])).toBe(5);
  });
  it('uses the one live runtime binding when no production label exists', () => {
    expect(productionBaseline([], [runtime({ version: 6 })])).toBe(6);
    expect(productionBaseline([{ name: 'staging', version: 7 }], [runtime({ label: 'staging' })])).toBe(7);
  });
  it('ignores retired and non-runtime bindings', () => {
    expect(productionBaseline([], [runtime({ version: 6 }), runtime({ version: 4 }, { retiredAt: '2026-11-01' }), { kind: 'dev', retiredAt: null, selector: { version: 2 } }])).toBe(6);
  });
  it('never takes the pinned application-verification target as production', () => {
    // The pinned verification target is a runtime binding on the recorded version; production is v6.
    expect(productionBaseline([], [runtime({ version: 4 }, { profile: 'native-application' }), runtime({ version: 6 }, { profile: 'support-app' })])).toBe(6);
    expect(productionBaseline([], [runtime({ version: 4 }, { profile: 'native-verification' })])).toBeUndefined();
  });
  it('offers no default when production is ambiguous or absent', () => {
    expect(productionBaseline([], [runtime({ version: 6 }), runtime({ version: 7 })])).toBeUndefined();
    expect(productionBaseline([], [])).toBeUndefined();
    expect(productionBaseline(undefined, undefined)).toBeUndefined();
  });
});
