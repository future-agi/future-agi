#!/usr/bin/env node
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { request } from "@playwright/test";

// Managed E2E backends disable HTTP/1 persistence to avoid reusing sockets at
// Granian's idle-close boundary. Do not send Connection: close ourselves: that
// would make this check pass even when the backend ignored the compose flag.
// Run after boot, not as readiness polling or against arbitrary attached stacks.
export async function checkApiConnectionPolicy(endpoint) {
  const context = await request.newContext();
  const responses = [];
  try {
    for (let i = 0; i < 3; i++) {
      const response = await context.get(endpoint, {
        maxRetries: 0,
        maxRedirects: 0,
        timeout: 10_000,
      });
      try {
        await response.body(); // An incomplete response is a failure too.
        assert.equal(response.status(), 200, "backend health status");
        const headers = response.headers();
        assert.equal(
          headers.connection?.trim().toLowerCase(),
          "close",
          "managed backend must advertise Connection: close",
        );
        responses.push({
          request: i + 1,
          status: response.status(),
          connection: headers.connection,
          server: headers.server,
        });
      } finally {
        await response.dispose();
      }
    }
    return responses;
  } finally {
    await context.dispose();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const endpoint = process.argv[2];
  if (!endpoint) throw new Error("Usage: check-api-connection-policy.mjs <health-url>");
  for (const response of await checkApiConnectionPolicy(endpoint)) {
    console.log(JSON.stringify(response));
  }
}
