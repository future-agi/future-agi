// Synthetic unit-test definitions; never loaded by product code.
export const oldHash = "a".repeat(64);
export const newHash = "b".repeat(64);
export const revision = {
  id: 17,
  server_id: "orders",
  tool: "lookup",
  revision: newHash,
  previous_revision: oldHash,
  reason: "changed",
  status: "pending",
  definition: {
    description: "Looks up and retains orders",
    inputSchema: { type: "object", required: ["id"] },
  },
  previous_definition: {
    description: "Looks up orders",
    inputSchema: { type: "object" },
  },
  diff: [
    {
      field: "description",
      before: "Looks up orders",
      after: "Looks up and retains orders",
    },
    {
      field: "inputSchema",
      before: { type: "object" },
      after: { type: "object", required: ["id"] },
    },
  ],
};
