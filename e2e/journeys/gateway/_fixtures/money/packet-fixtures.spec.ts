import { test, expect } from '@playwright/test';
import { j11NativePrograms, j12FaultPrograms } from './packet-fixtures';

test('MONEY-FIXTURE.J11-exact-native-program-inventory', () => {
  const programs = j11NativePrograms({ openai: 'fixture-openai', anthropic: 'fixture-anthropic', gemini: 'fixture-gemini' });
  expect(programs.map(p => p.id)).toEqual(['openai-json', 'openai-stream', 'messages-json', 'messages-stream',
    'gemini-native-json', 'caller-fallback']);
  expect(programs.map(p => p.attempts.map(a => [a.dialect, a.stream, a.inputTokens, a.outputTokens])))
    .toEqual([[['openai', false, 7, 7]], [['openai', true, 7, 7]], [['anthropic', false, 7, 7]],
      [['anthropic', true, 7, 7]], [['gemini', false, 7, 7]], [['openai', false, 7, 7], ['openai', false, 7, 7]]]);
  expect(programs[5].attempts.map(a => [a.target, a.outcome])).toEqual([['primary', 'http-503'], ['fallback', 'finish']]);
  expect(programs.every(p => p.productRowsCovered.length === 0)).toBe(true);
});

test('MONEY-FIXTURE.J12-exact-fault-program-inventory', () => {
  const programs = j12FaultPrograms('fixture-openai');
  expect(programs.map(p => p.id)).toEqual(['midstream-disconnect', 'delayed-usage', 'duplicate-usage-frame',
    'partial-usage', 'absent-usage', 'retry-fallback', 'cancel-before-usage', 'cancel-after-usage']);
  expect(programs[5].attempts.map(a => [a.target, a.outcome]))
    .toEqual([['primary', 'http-503'], ['primary', 'http-503'], ['fallback', 'finish']]);
  expect(programs.filter(p => p.id.startsWith('cancel-')).map(p => p.attempts[0].outcome))
    .toEqual(['cancel-before-usage', 'cancel-after-usage']);
  // Mutating a returned plan cannot change another cell or a future invocation.
  programs[0].attempts[0].inputTokens = 123;
  expect(programs[1].attempts[0].inputTokens).toBe(7);
  expect(j12FaultPrograms('fixture-openai')[0].attempts[0].inputTokens).toBe(7);
});
