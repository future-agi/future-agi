// Isolated Node assertion suite: no browser setup or developer dotenv files.
import { defineConfig } from "vitest/config";

export default defineConfig({
  envDir: false,
  test: {
    environment: "node",
    setupFiles: [],
    include: ["scripts/census-b-launch-build.test.js"],
    maxWorkers: 1,
    minWorkers: 1,
    testTimeout: 30000,
  },
});
