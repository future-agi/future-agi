// The attesting side of loopback-forwarder-lab.spec.ts: run as a child of the test
// worker (which holds the forwarder listeners and is named in E2E_LOOPBACK_FORWARDS),
// it attests one unpublished endpoint with lib/loopback-forwarder.ts exactly as the
// inspection does - real Docker inspect output, the real procfs, the default prober -
// and prints the verdict: { attestation: ACCEPT|REFUSE, reason?, receipt? }.
// Plan on stdin: { root, context, project, network, service, containerId,
// containerPort, hostPort, credentials }. Needs node --experimental-strip-types.
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const plan = JSON.parse(raw);
const { attestLoopbackForwards, linuxProc } = await import(
  pathToFileURL(`${plan.root}e2e/lib/loopback-forwarder.ts`).href
);
const docker = (...args) =>
  execFileSync("docker", ["--context", plan.context, ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
const [container] = JSON.parse(docker("inspect", plan.containerId));
const [net] = JSON.parse(docker("network", "inspect", plan.network));
let verdict;
try {
  const receipt = attestLoopbackForwards(
    [
      {
        service: plan.service,
        containerPort: plan.containerPort,
        hostPort: plan.hostPort,
      },
    ],
    {
      project: plan.project,
      network: plan.network,
      net,
      selected: { [plan.service]: container },
      inspect: (ids) => JSON.parse(docker("inspect", ...ids)),
      proc: linuxProc(),
      context: plan.context,
      credentials: plan.credentials,
    },
  );
  verdict = { attestation: "ACCEPT", receipt };
} catch (error) {
  const message = String(error?.message ?? error);
  if (!message.startsWith("STOP: managed mock ")) throw error;
  verdict = { attestation: "REFUSE", reason: message };
}
process.stdout.write(`${JSON.stringify(verdict)}\n`);
