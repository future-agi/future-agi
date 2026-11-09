import { execFileSync } from "node:child_process";

export const nodeVersion = () => execFileSync(process.execPath, ["--version"], { encoding: "utf8" }).trim();
