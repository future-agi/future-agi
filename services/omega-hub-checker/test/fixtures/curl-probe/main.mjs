// A network probe: it tries every way out and prints which ones were stopped. With no key and no
// network it must print "stopped" for each; anything else means the sandbox leaked.
import { runProbes } from "./probes.mjs";

const results = await runProbes();
for (const result of results) console.log(`${result.name}: ${result.reached ? "REACHED" : "stopped"}`);
