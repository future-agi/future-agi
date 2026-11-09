// Build/launch attestation for locally built walk images. No registry reads.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function attestImages(built, configured, running) {
  return Object.entries(built).map(([service, image]) => {
    if (!/^sha256:[a-f0-9]{64}$/.test(image.id))
      throw new Error(`invalid built image digest: ${service}`);
    if (configured[service]?.image !== image.tag)
      throw new Error(`not the head tag: ${service}`);
    const matches = running.filter(
      (c) => c.Config.Labels["com.docker.compose.service"] === service,
    );
    if (
      matches.length !== 1 ||
      !matches[0].State.Running ||
      matches[0].Image !== image.id ||
      matches[0].Config.Image !== image.tag
    )
      throw new Error(`running image is not the build: ${service}`);
    return { service, tag: image.tag, imageId: image.id };
  });
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [mode, stack, head, file] = process.argv.slice(2);
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const run = (cmd, args) =>
    execFileSync(cmd, args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  const tag = `walk-${head.slice(0, 12)}`;
  const tags =
    stack === "standalone"
      ? { app: "futureagi/standalone:e2esa" }
      : {
          backend: `futureagi/future-agi:${tag}`,
          frontend: `futureagi/frontend:${tag}`,
          "fi-collector": `futureagi/fi-collector:${tag}`,
          "agentcc-gateway": `futureagi/agentcc-gateway:${tag}`,
        };
  if (mode === "built") {
    const built = Object.fromEntries(
      Object.entries(tags).map(([service, tag]) => {
        const [image] = JSON.parse(run("docker", ["image", "inspect", tag]));
        return [service, { tag, id: image.Id }];
      }),
    );
    writeFileSync(file, JSON.stringify({ head, built }, null, 2));
  } else if (mode === "running") {
    const manifest = JSON.parse(readFileSync(file, "utf8"));
    if (manifest.head !== head) throw new Error("image manifest head mismatch");
    const config = JSON.parse(
      run(root + "bin/e2e", ["compose", "config", "--format", "json"]),
    );
    const ids = run(root + "bin/e2e", [
      "compose",
      "ps",
      "-q",
      ...Object.keys(manifest.built),
    ])
      .trim()
      .split(/\s+/);
    const images = attestImages(
      manifest.built,
      config.services,
      JSON.parse(run("docker", ["inspect", ...ids])),
    );
    process.stdout.write(JSON.stringify({ head, images }, null, 2) + "\n");
  } else throw new Error("expected built or running");
}
