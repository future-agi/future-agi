// THROWAWAY: proves the E2E gate fails closed. Reverted in the next commit.
import { test, expect } from '@playwright/test';

test('throwaway failure: E2E Tests Pass must be red', () => {
  expect(1).toBe(2);
});
