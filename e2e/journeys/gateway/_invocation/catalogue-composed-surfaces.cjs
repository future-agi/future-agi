"use strict";
const { composed } = require("./catalogue-composed-core.cjs");
const entries = [];
const j14 = (spec, title, phase = "supplemental", options = {}) =>
  entries.push(
    composed("J14", `J14/${spec}.spec.ts`, title, phase, {
      annotations: ["journey-subassertion"],
      ...options,
    }),
  );
// These two use a real browser, but ONLY against their fixture-owned loopback
// OAuth/consent/callback documents. They are not the anonymous stock product page.
for (const scope of ["A1", "A2"]) {
  j14(
    "local-fixtures",
    `J14-FIXTURE native OAuth ${scope}: DCR, consent, callback, rotation and revoke`,
    "supplemental",
    {
      network: "own-host",
      browserContextUsed: true,
      requirements: [
        "admitted fixtureBrowser viewport",
        "guarded loopback listeners and browser contexts",
      ],
    },
  );
}
for (const title of [
  "J14-FIXTURE rejects nonlocal DCR redirect before any redirect or grant",
  "J14-FIXTURE native receiver refuses wrong cookie, wrong state and replay",
  "J14-FIXTURE principal isolation, revoke and duplicate calls stay visible",
])
  j14("local-fixtures", title, "supplemental", { network: "own-host" });
for (const title of [
  "J14-FIXTURE OAuth clock, PKCE, foreign client and concurrent redemption controls",
  "J14-FIXTURE approval API contract is explicitly unavailable at prep pin",
  "J14-FIXTURE audit pagination retains every row and tenant/run bindings",
])
  j14("local-fixtures", title);
for (const name of ["agentcc-gateway", "agentcc-gateway-probe"]) {
  j14(
    "mcp-negotiation",
    `J14-FIXTURE ${name} default revision negotiates a usable legacy session`,
    "supplemental",
    { network: "own-host" },
  );
}
for (const phase of ["baseline", "no-op-revoke", "restored"]) {
  j14(
    "revoke-controls",
    `J14-FIXTURE RFC7009 independent effect detector [${phase}]`,
    phase === "no-op-revoke" ? "negative" : phase,
  );
}
const j15 = (title, row, network = "none") =>
  entries.push(
    composed(
      "J15",
      "J15/sinks-catalogue-fixtures.spec.ts",
      title,
      "supplemental",
      {
        rows: [row],
        annotations: ["journey-fragment"],
        network,
      },
    ),
  );
j15(
  "F13 fixture OTLP rejects malformed protobuf and duplicate attributes",
  "W13.A04",
);
j15(
  "F13 fixture loopback OTLP rejects JSON and reports privacy violations without bodies",
  "W13.A04",
  "own-host",
);
for (const consent of [false, true])
  for (const operatorBodies of [false, true]) {
    j15(
      `F13 privacy oracle conjunction consent=${consent} operator=${operatorBodies}`,
      "W13.A04",
    );
  }
for (const vendor of ["datadog", "mixpanel", "posthog"]) {
  j15(
    `F15 ${vendor} projection preserves extra IDs, multiplicity and failures`,
    "W17.A03",
  );
}
j15(
  "F15 PostHog complete history distinguishes exact validation from deliveries",
  "W17.A03",
);
j15(
  "F15 storage projection rejects prefix escapes and keeps duplicate deliveries",
  "W17.A03",
);
j15(
  "F15 sync API walks every page and refuses drift and duplicates",
  "W17.A02",
);
for (const title of [
  "F14 committed stock inventory and router spelling set are source-bound",
  "F14 pinned tilde-prefixed model identities survive complete source validation",
  "F14 independent decimal price units refuse output-image and generation inference",
])
  entries.push(
    composed("J31", "J31/catalogue-source.spec.ts", title, "supplemental", {
      rows: ["A04", "A05", "A06", "A08"],
      annotations: Array(4).fill("journey-fragment"),
      requirements: [
        "pinned public catalogue source blobs in accepted read closure; no lazy fetch",
      ],
    }),
  );
module.exports = entries;
