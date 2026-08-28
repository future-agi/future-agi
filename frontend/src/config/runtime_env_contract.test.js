import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const runtimeLimitsSource = readFileSync(
  resolve(process.cwd(), "src/config/runtime_limits.js"),
  "utf8",
);
const containerEntrypointSource = readFileSync(
  resolve(process.cwd(), "docker-entrypoint.sh"),
  "utf8",
);
const exampleEnvironmentSource = readFileSync(
  resolve(process.cwd(), ".env.example"),
  "utf8",
);

const runtimeNames = Array.from(
  runtimeLimitsSource.matchAll(/"(VITE_[A-Z0-9_]+)"/g),
  (match) => match[1],
);

describe("frontend runtime environment contract", () => {
  it.each(runtimeNames)(
    "exposes %s through the container entrypoint",
    (name) => {
      expect(containerEntrypointSource).toMatch(new RegExp(`^${name}$`, "m"));
    },
  );

  it.each(runtimeNames)("documents %s in the example environment", (name) => {
    expect(exampleEnvironmentSource).toMatch(new RegExp(`^${name}=`, "m"));
  });
});
