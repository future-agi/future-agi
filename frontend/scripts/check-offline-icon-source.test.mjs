import assert from "node:assert/strict";
import { test } from "node:test";
import { assertOfflineIconInventory } from "./check-offline-icon-source.mjs";

function fixture() {
  const names = Array.from({ length: 1001 }, (_, i) => `mdi:fixture-${i}`);
  return {
    inventory: {
      names,
      backend: {
        names: [
          "mdi:database-plus",
          "mdi:bug-check-outline",
          "mdi:compare-arrows",
          "mdi:star",
        ],
        sources: [{ file: "../futureagi/builtin_skills/example.yaml" }],
      },
      unresolved: [],
      templates: [
        { names: ["circle-flags:us"] },
        { names: ["fluent:chevron-up-16-filled"] },
      ],
    },
    registry: Object.fromEntries(
      names.map((name) => [name, { body: "<path/>" }]),
    ),
  };
}

test("accepts the complete source contract", () => {
  const { inventory, registry } = fixture();
  assert.doesNotThrow(() => assertOfflineIconInventory(inventory, registry));
});

const controls = [
  [
    "truncated inventory",
    ({ inventory }) => inventory.names.pop(),
    /full icon inventory/,
  ],
  ...[
    "mdi:database-plus",
    "mdi:bug-check-outline",
    "mdi:compare-arrows",
    "mdi:star",
  ].map((name) => [
    `missing backend icon ${name}`,
    ({ inventory }) => {
      inventory.backend.names = inventory.backend.names.filter(
        (v) => v !== name,
      );
    },
    /Backend icon/,
  ]),
  [
    "missing skill sources",
    ({ inventory }) => {
      inventory.backend.sources = [];
    },
    /skill icon sources/,
  ],
  [
    "unresolved template",
    ({ inventory }) => {
      inventory.unresolved = [{ names: null }];
    },
    /Unresolved/,
  ],
  [
    "missing registry member",
    ({ registry }) => {
      delete registry["mdi:fixture-500"];
    },
    /missing from the offline registry/,
  ],
  [
    "missing registry",
    (data) => {
      data.registry = {};
    },
    /missing from the offline registry/,
  ],
  [
    "missing country template",
    ({ inventory }) => inventory.templates.shift(),
    /computed country flags/,
  ],
  [
    "missing chevron template",
    ({ inventory }) => inventory.templates.pop(),
    /computed chevrons/,
  ],
];

for (const [name, mutate, message] of controls) {
  test(`rejects ${name}`, () => {
    const data = fixture();
    mutate(data);
    assert.throws(
      () => assertOfflineIconInventory(data.inventory, data.registry),
      message,
    );
  });
}
