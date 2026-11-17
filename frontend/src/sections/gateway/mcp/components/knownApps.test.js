import { describe, expect, it } from "vitest";

import { appForUrl } from "./knownApps";

describe("appForUrl: the app an MCP server belongs to, by its URL's domain", () => {
  it("knows the major apps' MCP endpoints", () => {
    expect(appForUrl("https://api.githubcopilot.com/mcp/")).toMatchObject({
      name: "GitHub",
      logo: "/assets/icons/app/ic_github_grey.svg",
    });
    expect(appForUrl("https://mcp.stripe.com")).toMatchObject({
      name: "Stripe",
      logo: null,
    });
    expect(appForUrl("https://mcp.linear.app/sse")?.name).toBe("Linear");
    expect(appForUrl("https://huggingface.co/mcp")?.name).toBe("Hugging Face");
  });

  // The server id is the org's own name for it: never a reason to show an
  // app's logo. Only the URL's domain counts, and only the whole domain.
  it("never guesses from a look-alike or an internal host", () => {
    expect(appForUrl("http://demo-upstream:8080/mcp-crm")).toBeNull();
    expect(appForUrl("https://mcp.example.com/github/mcp")).toBeNull();
    expect(appForUrl("https://evilstripe.com/mcp")).toBeNull();
    expect(appForUrl("https://stripe.com.attacker.io/mcp")).toBeNull();
    expect(appForUrl("")).toBeNull();
    expect(appForUrl("not a url")).toBeNull();
  });
});
