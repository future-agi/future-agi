#!/usr/bin/env node
// An E2E CI leg builds the stack's images from its checkout and then boots the
// stack from the local Docker store. `record FILE` runs after the builds: it
// fails if a built image is not in that store, and writes each one's image id.
// `verify FILE` runs after the boot: it fails if a container of the stack runs
// another image under one of those tags, or if none of them runs at all.
// Image references and ids only; nothing else of a container is read.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

/** The images the build steps of .github/workflows/e2e-ci.yml tag, in order. */
export function builtRefs(env) {
  const tag = env.IMAGE_TAG;
  if (!tag) throw new Error("built-images: IMAGE_TAG is not set");
  const refs = [`futureagi/code-executor:${tag}`];
  if (env.BUILD_BACKEND === "true")
    refs.push(`futureagi/future-agi:${tag}`, `futureagi/fi-collector:${tag}`);
  if (env.BUILD_FRONTEND === "true") refs.push(`futureagi/frontend:${tag}`);
  if (env.BUILD_GATEWAY === "true")
    refs.push(`futureagi/agentcc-gateway:${tag}`);
  if (env.E2E_STACK === "standalone") refs.push(`futureagi/standalone:${tag}`);
  return refs;
}

/** `inspect(ref)` gives the image id, or undefined when the store has no such image. */
export function record(refs, inspect) {
  const built = Object.fromEntries(refs.map((ref) => [ref, inspect(ref)]));
  const missing = refs.filter((ref) => !built[ref]);
  if (missing.length)
    throw new Error(
      `built-images: not in the local Docker store: ${missing.join(", ")}`,
    );
  return built;
}

/** `containers` are {ref, id}: the image a container was started from, as named and as resolved. */
export function verify(built, containers) {
  const running = new Set();
  for (const { ref, id } of containers) {
    if (!(ref in built)) continue;
    if (id !== built[ref])
      throw new Error(
        `built-images: ${ref} runs ${id}, this job built ${built[ref]}`,
      );
    running.add(ref);
  }
  if (!running.size)
    throw new Error(
      "built-images: the stack runs none of the images this job built",
    );
  return [...running].sort();
}

const docker = (...args) =>
  execFileSync("docker", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });

function main([command, file]) {
  if (!["record", "verify"].includes(command) || !file)
    throw new Error("usage: built-images.mjs record|verify FILE");
  if (command === "record") {
    const built = record(builtRefs(process.env), (ref) => {
      try {
        return docker("image", "inspect", "--format", "{{.Id}}", ref).trim();
      } catch {
        return undefined;
      }
    });
    writeFileSync(file, `${JSON.stringify(built, null, 2)}\n`);
    console.log(`Images this job built:\n${JSON.stringify(built, null, 2)}`);
    return;
  }
  const built = JSON.parse(readFileSync(file, "utf8"));
  const e2e = fileURLToPath(new URL("../../bin/e2e", import.meta.url));
  const ids = execFileSync(e2e, ["compose", "ps", "--all", "--quiet"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  })
    .split("\n")
    .filter(Boolean);
  const containers = ids.length
    ? docker("inspect", "--format", "{{.Config.Image}}\t{{.Image}}", ...ids)
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const [ref, id] = line.split("\t");
          return { ref, id };
        })
    : [];
  const running = verify(built, containers);
  console.log(
    `The stack runs the images this job built: ${running.join(", ")}`,
  );
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exit(1);
  }
}
