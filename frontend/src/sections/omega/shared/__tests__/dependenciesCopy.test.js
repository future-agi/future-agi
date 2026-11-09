import { describe, expect, it } from "vitest";

import { customerCopy, isCustomerSafe } from "../omegaReasons";
import { OMEGA_STATE } from "../omegaStates";

// EE refuses a bundle whose package.json depends on a package the runtime does
// not provide (`dependencies_unsupported`). EE's words describe its roadmap
// ("cannot install packages yet ... today"), so Future AGI shows its own copy:
// what deploys and what to change (HIDE-UNFINISHED-FA; console-reality audit,
// deployments row "Projects with npm dependencies").

const EE_MESSAGE =
  "Deployments here cannot install packages yet: package.json depends on lodash-es. Only @omega/* dependencies (the runtime provides them) deploy today.";

describe("dependencies_unsupported", () => {
  it("replaces EE's roadmap wording even though it passes the COPY1 check", () => {
    expect(isCustomerSafe(EE_MESSAGE)).toBe(true);
    const copy = customerCopy({
      status: 400,
      code: "dependencies_unsupported",
      serverMessage: EE_MESSAGE,
      fallbackState: OMEGA_STATE.ERROR,
    });
    expect(copy.title).toBe("This project has package dependencies");
    expect(copy.message).toContain("@omega/*");
    expect(copy.message).toContain("Remove the other dependencies");
    expect(copy.message).not.toMatch(/\byet\b|\btoday\b/);
    expect(isCustomerSafe(copy.message)).toBe(true);
  });

  it("leaves other codes' safe server messages as they were", () => {
    const copy = customerCopy({
      status: 400,
      code: "something_else",
      serverMessage: "The name is taken.",
      fallbackState: OMEGA_STATE.ERROR,
    });
    expect(copy.message).toBe("The name is taken.");
  });
});
