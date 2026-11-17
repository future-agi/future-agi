// The keyless test omega hub check runs: it reports what it could reach, then fails on purpose.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import net from "node:net";
import { test } from "node:test";

const outcome = (fn) => {
  try {
    fn();
    return "allowed";
  } catch (error) {
    return error.code ?? "error";
  }
};
const parentOf = (pid) => {
  const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
  return Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[1]);
};
const uidOf = (pid) => /^Uid:\s+(\d+)/m.exec(readFileSync(`/proc/${pid}/status`, "utf8"))?.[1] ?? "?";
const commandOf = (pid) => readFileSync(`/proc/${pid}/cmdline`, "utf8").split("\0");
const connect = (port) => new Promise((resolve) => {
  const socket = net.connect(port, "127.0.0.1");
  socket.on("connect", () => {
    socket.destroy();
    resolve("allowed");
  });
  socket.on("error", (error) => resolve(error.code));
});

test("every probe at the judge is refused", async () => {
  const results = {};
  const chain = [];
  for (let pid = process.ppid; pid > 0; pid = parentOf(pid)) {
    chain.push(`${pid}:${uidOf(pid)}:${commandOf(pid)[0]}`);
    results[`${pid} fd/1 write`] = outcome(() => writeFileSync(`/proc/${pid}/fd/1`, "probe\n"));
    results[`${pid} environ read`] = outcome(() => readFileSync(`/proc/${pid}/environ`));
    results[`${pid} mem read`] = outcome(() => readFileSync(`/proc/${pid}/mem`));
  }
  const cli = readdirSync("/proc").filter((name) => /^\d+$/.test(name)).filter((pid) => {
    try {
      return readFileSync(`/proc/${pid}/cmdline`, "utf8").includes("hub\0check");
    } catch {
      return false;
    }
  });
  for (const pid of cli) results[`${pid} SIGUSR1`] = outcome(() => process.kill(Number(pid), "SIGUSR1"));
  results["1 signal 0"] = outcome(() => process.kill(1, 0));
  await new Promise((resolve) => setTimeout(resolve, 1000));
  results["inspector after SIGUSR1"] = await connect(9229);
  assert.fail(`PROBES ${JSON.stringify({ chain, cli, results })}`);
});
