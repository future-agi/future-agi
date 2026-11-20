import { describe, expect, it } from "vitest";
import { defaultsAfterSave } from "../defaultAfterSave";

const v = (n, extra = {}) => ({ label: `v${n}`, ...extra });
const envs = [
  { id: "a", name: "Follows production" },
  { id: "b", name: "Pinned" },
];

describe("defaultsAfterSave", () => {
  it("says the new version is the default where it follows production", () => {
    const byEnv = {
      a: { agent: {}, agentVersions: [v(1), v(2)] },
      b: {
        agent: {},
        agentVersions: [v(1), v(2)],
        agentVersionPinned: true,
        activeAgentVersion: "v1",
      },
    };
    const [a, b] = defaultsAfterSave(envs, byEnv, v(2));
    expect(a).toMatchObject({ isNew: true, label: "v2" });
    expect(b).toMatchObject({ isNew: false, label: "v1" });
    expect(b.why).toMatch(/Pinned to v1 here, so it stays/);
  });

  it("explains a draft that isn't the default anywhere yet", () => {
    const byEnv = {
      a: { agent: {}, agentVersions: [v(1), v(2, { status: "draft" })] },
    };
    const [a] = defaultsAfterSave(
      envs.slice(0, 1),
      byEnv,
      v(2, { status: "draft" }),
    );
    expect(a.isNew).toBe(false);
    expect(a.why).toMatch(/draft/);
  });
});
