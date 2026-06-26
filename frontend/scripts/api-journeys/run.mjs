/* eslint-disable no-console */
import process from "node:process";
import { runJourneys } from "./lib/runner.mjs";
import { journeys } from "./registry.mjs";

runJourneys(journeys).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
