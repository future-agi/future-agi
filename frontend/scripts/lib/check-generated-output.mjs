import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const snapshot = (directory) => new Map(
  fs.existsSync(directory)
    ? fs.readdirSync(directory)
      .filter((name) => name.endsWith(".ts"))
      .map((name) => [name, fs.readFileSync(path.join(directory, name), "utf8")])
    : [],
);

// A check must never remove or rewrite the clients that builds, tests and dev
// servers are reading. Keep the output/mutator layout identical so Orval emits
// the same relative import without touching the live source tree.
export async function checkGeneratedOutput({ outputDir, mutatorPath, generate }) {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "openapi-check-"));
  const temporaryOutput = path.join(temporaryRoot, "src", "generated", "api-contracts");
  const temporaryMutator = path.resolve(
    temporaryOutput,
    path.relative(outputDir, mutatorPath),
  );
  try {
    fs.mkdirSync(path.dirname(temporaryMutator), { recursive: true });
    fs.copyFileSync(mutatorPath, temporaryMutator);
    await generate(temporaryOutput, temporaryMutator);
    // Compare with the current checkout, not a snapshot taken before a long
    // generation: another editor may have changed a client in the meantime.
    const before = snapshot(outputDir);
    const after = snapshot(temporaryOutput);
    return [...new Set([...before.keys(), ...after.keys()])].filter(
      (name) => before.get(name) !== after.get(name),
    );
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
}
