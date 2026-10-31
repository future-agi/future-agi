// These pages use the shared pieces: ConfirmDialog for Uninstall and Disconnect, pageBackButtonSx for the back button.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PAGES = [
  "github/GithubIntegrationPage.jsx",
  "slack/SlackIntegrationPage.jsx",
  "coding-agents/CodingAgentsIntegrationPage.jsx",
];

describe("integration page confirmations", () => {
  it.each(PAGES)("%s confirms through ConfirmDialog", (page) => {
    const source = fs.readFileSync(path.join(root, page), "utf8");
    expect(source).toMatch(/from "src\/components\/custom-dialog"/);
    expect(source).not.toMatch(/\bDialogActions\b/);
  });

  it.each([...PAGES, "../IntegrationDetail.jsx"])(
    "%s styles its back button with pageBackButtonSx",
    (page) => {
      const source = fs.readFileSync(path.join(root, page), "utf8");
      const backButtons =
        source.match(/<Button\b(?:(?!<\/Button>).)*?Back to Integrations/gs) ||
        [];
      expect(backButtons.length).toBeGreaterThan(0);
      for (const button of backButtons)
        expect(button).toMatch(/pageBackButtonSx/);
    },
  );
});
