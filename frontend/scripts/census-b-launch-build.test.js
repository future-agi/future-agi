// Run only through vitest.census-launch.config.js in the authorized cloud stage.
// This builds the actual gate, not a copied predicate. No application plugins,
// .env files, writeBundle hooks, source-map upload or files emitted on disk.
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { build } from "vite";
import { expect, it } from "vitest";
import appConfig from "../vite.config.js";

const HOLDS = [
  "list.organize.folders",
  "dataset.splits",
  "dataset.settings",
  "exp.new.scope",
  "exp.new.splitScope",
  "exp.runControl",
  "evals.dag",
];
it.each(["production", "test"])(
  "application build mode %s ignores even an explicit census opt-in",
  async (mode) => {
    expect(appConfig.define.__DSX_CENSUS_B_TEST_BUILD__).toBe("false");
    const built = await build({
      configFile: false,
      envDir: false,
      publicDir: false,
      mode,
      logLevel: "silent",
      plugins: [],
      resolve: {
        alias: { src: fileURLToPath(new URL("../src", import.meta.url)) },
      },
      define: {
        ...appConfig.define,
        "import.meta.env.VITE_DSX_CENSUS_B_TEST": JSON.stringify("true"),
      },
      build: {
        write: false,
        emptyOutDir: false,
        sourcemap: false,
        minify: false,
        lib: {
          entry: fileURLToPath(
            new URL("../src/launch/launchSwitch.js", import.meta.url),
          ),
          name: "CensusLaunchGate",
          formats: ["iife"],
        },
      },
    });
    const outputs = Array.isArray(built)
      ? built.flatMap((result) => result.output)
      : built.output;
    const chunks = outputs.filter(
      (output) => output.type === "chunk" && output.isEntry,
    );
    expect(chunks).toHaveLength(1);
    const sandbox = {};
    runInNewContext(chunks[0].code, sandbox, { timeout: 1000 });
    const gate = sandbox.CensusLaunchGate;
    expect(gate.censusBTestOptIn()).toBe(false);
    const state = {
      launch: true,
      experimentsBasic: true,
      madeReal: new Set(HOLDS),
    };
    gate.setLaunchOverrideForTest(state);
    expect(gate.isLaunchBuild()).toBe(true);
    for (const id of HOLDS) {
      expect(gate.surfaceShownIn(id, state), id).toBe(false);
      expect(gate.isSurfaceShown(id), id).toBe(false);
    }
    expect(gate.surfaceShownIn("list.organize.metadata", state)).toBe(false);
  },
);
