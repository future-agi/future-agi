import { describe, it, expect } from "vitest";
import { writableOrgConfig } from "./orgConfig";

describe("writableOrgConfig", () => {
  it("keeps only writable sections, under their snake_case names", () => {
    expect(
      writableOrgConfig({
        id: "config-1",
        version: 3,
        is_active: true,
        providers: { openai: {} },
        ipAcl: { enabled: true },
        tool_policy: { default: "deny" },
        cache: { enabled: false },
      }),
    ).toEqual({
      ip_acl: { enabled: true },
      tool_policy: { default: "deny" },
      cache: { enabled: false },
    });
  });

  it("leaves out sections the config doesn't have so the backend defaults apply", () => {
    expect(writableOrgConfig(null)).toEqual({});
    expect(writableOrgConfig({ budgets: null, alerting: undefined })).toEqual(
      {},
    );
  });
});
