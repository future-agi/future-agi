import { describe, expect, it } from "vitest";
import {
  agentDiff,
  copyAllSetup,
  orderLine,
  setupReady,
  toolWrapper,
  tryNoKeysCommand,
} from "../setupRecipe";

describe("IT-R5 G1 P3 setup recipe", () => {
  it("the no-keys try runs one task", () => {
    expect(
      tryNoKeysCommand({ dryRunCommand: "python run_and_upload.py --dry-run" }),
    ).toBe("python run_and_upload.py --dry-run --limit 1");
    expect(tryNoKeysCommand({ dryRunCommand: "x --limit 3" })).toBe(
      "x --limit 3",
    );
    expect(tryNoKeysCommand({})).toBeNull();
  });

  it("copy-all masks keys on screen and carries them on copy", () => {
    const r = copyAllSetup({
      install: { code: "export K=••••", copyText: "export K=sk-real" },
      sandbox: { code: "docker run sb" },
      tryCommand: "python run.py --limit 1",
    });
    expect(r.shown).toContain("export K=••••");
    expect(r.shown).not.toContain("sk-real");
    expect(r.copy).toContain("export K=sk-real");
    expect(r.copy).toContain("# 2. Start the sandbox");
    expect(r.copy).toContain("# 3. Run 1 task");
    expect(r.hasKeys).toBe(true);
    const ns = copyAllSetup({
      install: { code: "pip install x" },
      sandbox: { code: "docker run sb" },
      tryCommand: "run",
      noSandbox: true,
    });
    expect(ns.copy).not.toContain("sandbox");
    expect(ns.copy).toContain("# 2. Run 1 task");
    expect(ns.hasKeys).toBe(false);
  });

  it("the order line says the run is added first", () => {
    expect(orderLine()).toMatch(/^Add the run to the experiment first/);
    expect(orderLine()).toContain("run not found");
  });

  it("the agent diff reads the call and answer lines", () => {
    const file = [
      "def run_rows(rows):",
      "    for row in rows:",
      "        out = graph.invoke({'input': text})",
      "        output, error = out['reply'], None",
    ].join("\n");
    const d = agentDiff(file, "python");
    expect(d.before).toHaveLength(2);
    expect(d.after).toHaveLength(2);
    expect(d.fn).toBe("run_rows()");
    expect(agentDiff("nothing here")).toBeNull();
  });

  it("the tool wrapper routes to the sandbox URL in 6 lines", () => {
    const py = toolWrapper({ envVar: "ACME_SANDBOX_URL", tool: "refund" });
    expect(py.split("\n")).toHaveLength(6);
    expect(py).toContain('{SANDBOX}/tools/refund"');
    const ts = toolWrapper({ tool: "look_up", lang: "typescript" });
    expect(ts.split("\n")).toHaveLength(6);
    expect(ts).toContain("lookUp");
  });

  it("steps 2-4 ready only with proof (or sandbox skipped)", () => {
    const steps = ["file", "install", "sandbox", "agent", "run"];
    expect(setupReady({}, { steps })).toBe(false);
    expect(
      setupReady({ install: 1, agent: 1 }, { steps, noSandbox: true }),
    ).toBe(true);
    expect(setupReady({ install: 1, agent: 1 }, { steps })).toBe(false);
  });
});

// options-truth L2 G4 fix 6: the setup script names the dataset's unit.
describe("copyAllSetup names the dataset's noun", () => {
  it("a row dataset runs 1 row, a task suite 1 task", () => {
    const install = { code: "pip install futureagi" };
    const tryCommand =
      "python run_and_upload.py --agent agent.py:graph --limit 1";
    expect(
      copyAllSetup({ install, tryCommand, rowNoun: "row" }).copy,
    ).toContain("# 2. Run 1 row\n");
    expect(copyAllSetup({ install, tryCommand }).copy).toContain(
      "# 2. Run 1 task\n",
    );
  });
});
