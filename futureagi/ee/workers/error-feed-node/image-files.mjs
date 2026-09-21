// Explicit deployment allowlist: never copy research corpora, tests or secrets.
export const workerFiles = [
  'gateway-provider.mjs','worker.mjs','daemon.mjs','control-client.mjs',
  'coordinator.mjs','evidence-store.mjs','investigation.mjs','audio-inspection.mjs','grouping-daemon.mjs',
  ...['snapshot','features','embedding-client','lsh','engine','policy',
    'gateway','coordinator','wakeup','outbox','context-budget','request-limits','severity'].map(name=>`grouping/${name}.mjs`),
  ...['admission','citation-repair','common','companion-context','input','pair-model',
    'pipeline','provider','registry','retrieval','targeted-revisit'].map(name=>`grouping/f6/${name}.mjs`),
];
