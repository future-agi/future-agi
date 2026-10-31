// Explicit deployment allowlist: never copy research corpora, tests or secrets.
export const workerFiles = [
  'observability.mjs','gateway-provider.mjs','worker.mjs','daemon.mjs','control-client.mjs',
  'coordinator.mjs','evidence-store.mjs','simulation-evidence.mjs','investigation.mjs','audio-inspection.mjs','grouping-daemon.mjs',
  ...['snapshot','features','embedding-client','lsh','engine','policy',
    'gateway','coordinator','checkpoint','wakeup','outbox','context-budget','request-limits','severity',
    'control-conflict-reasons'].map(name=>`grouping/${name}.mjs`),
  ...['admission','behavior-catalog','citation-repair','common','companion-context','input','pair-model',
    'pipeline','provider','registry','retrieval','targeted-revisit'].map(name=>`grouping/f6/${name}.mjs`),
];
