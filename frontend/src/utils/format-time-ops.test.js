import { describe, expect, it } from 'vitest';
import { fDateTimeUTC, fObservationAge } from './format-time';

describe('operations receipt times', () => {
  it('formats fractional API timestamps in UTC without depending on the viewer timezone', () => {
    expect(fDateTimeUTC('2026-11-13T01:46:04.943460Z')).toBe('13 Nov 2026, 01:46 UTC');
    expect(fDateTimeUTC('2026-11-13T07:16:04+05:30')).toBe('13 Nov 2026, 01:46 UTC');
    expect(fDateTimeUTC('2026-11-13T00:00:00Z')).toBe('13 Nov 2026, 00:00 UTC');
  });
  it.each([null, undefined, 'now', '2026-02-30T01:00:00Z', '2026-11-13', 0])('does not fabricate a date for %s', (value) => {
    expect(fDateTimeUTC(value)).toBe('Time not reported');
  });
  it.each([[0, '0s'], [3.447751, '3s'], [59.9999, '59s'], [60, '1min'], [3599, '59min'], [3600, '1h'], [86400, '24h']])('formats %s snapshot seconds as whole units', (value, expected) => {
    expect(fObservationAge(value)).toBe(`Updated ${expected} ago`);
  });
  it.each([null, undefined, -1, Infinity, NaN, '3.4'])('keeps unreported ages unknown: %s', (value) => {
    expect(fObservationAge(value)).toBe('Time not reported');
  });
});
