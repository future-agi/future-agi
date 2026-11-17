// HR-JRN-01: a custom reporter that flushes late (value-free/boundary.config.ts,
// probe `late-reporter`). It writes the probe value into the publish directory in its
// onExit (after the value-free reporter's assembly) and again from an exit hook it
// registers then (after the value-free runner's own exit hook). Value-free mode refuses
// any custom reporter; the runner's exit check catches the first write and the
// launcher's seal check (lib/value-free-seal.mjs) the second.
const { writeFileSync } = require("node:fs");
const path = require("node:path");

module.exports = class LateReporter {
  constructor() {
    const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES || "{}");
    this.publish = probes.publish;
    this.value = probes.value;
  }

  printsToStdio() {
    return false;
  }

  async onExit() {
    const write = (name) => {
      try {
        writeFileSync(path.join(this.publish, name), this.value);
      } catch {
        // the directory may not exist: nothing published, nothing to break
      }
    };
    write("late-onexit.txt");
    process.on("exit", () => write("late-exit.txt"));
  }
};
