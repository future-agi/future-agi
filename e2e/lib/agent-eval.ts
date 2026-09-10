import { expect, type Page } from '@playwright/test';

// The unentitled half of every agent-eval flow.
//
// `agentic_eval` is oss_locked (futureagi/tfc/capabilities/registry.py), so a
// deployment without a license naming it cannot author an agent eval at all.
// That is product behaviour worth asserting rather than skipping: a regression
// that re-opened the tab without an entitlement would otherwise pass unnoticed.
//
// Gate the caller on `capabilities.agentic_eval`, never on deployment mode —
// mode only reports that EE_LICENSE_KEY is set, not what it grants.
export async function assertAgentTabLocked(page: Page): Promise<void> {
  await page.goto('/dashboard/evaluations/create');
  await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: 15_000 });

  // EvalCreatePage defaults evalType to "llm" instead of "agent" once denial is
  // confirmed (:268), so the judge editor — not the agent one — is mounted.
  await expect(page.getByRole('tab', { name: 'LLM-As-A-Judge' }))
    .toHaveAttribute('aria-selected', 'true', { timeout: 15_000 });

  // When locked the tab is wrapped in a CustomTooltip, and the tooltip title
  // becomes the tab's ACCESSIBLE NAME — it renders as
  // `tab "Agent evaluations aren't enabled for this workspace.": Agents`,
  // not `tab "Agents"`. So the name is itself proof of the locked state, and a
  // plain getByRole('tab', { name: 'Agents' }) finds nothing here by design.
  const agentTab = page.getByRole('tab', {
    name: "Agent evaluations aren't enabled for this workspace.",
  });
  await expect(agentTab).toBeVisible();
  await expect(agentTab).toHaveAttribute('aria-selected', 'false');

  // Clicking is swallowed: onChange returns early with a snackbar rather than
  // switching (:892-899). The tab staying unselected is the real assertion —
  // the snackbar is presentation.
  await agentTab.click();
  await expect(agentTab).toHaveAttribute('aria-selected', 'false');
  await expect(page.getByRole('tab', { name: 'LLM-As-A-Judge' }))
    .toHaveAttribute('aria-selected', 'true');
}
