import { useEffect, useState } from "react";
import { resolveAttachments } from "./client";
import { peakDescriptor, decodePeaks, peakOverview } from "./recordingPeaks";
async function readPeaks(path, signal) {
  const response = await fetch(path, {
    credentials: "same-origin",
    mode: "same-origin",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
    signal,
  });
  if (
    response.status !== 200 ||
    response.headers.get("Content-Type") !== "application/octet-stream"
  )
    throw Error("No peaks");
  const length = Number(response.headers.get("Content-Length")),
    d = peakDescriptor(response.headers.get("X-Attachment-Peaks"), length);
  // Cap actual bytes too: Content-Length isn't an allocation authority.
  const reader = response.body.getReader(),
    bytes = new Uint8Array(length);
  let used = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (used + value.length > length) throw Error("Invalid length");
      bytes.set(value, used);
      used += value.length;
    }
  } finally {
    reader.releaseLock();
  }
  if (used !== length) throw Error("Invalid length");
  return decodePeaks(d, bytes.buffer);
}
export default function useRecordingPeaks({
  projectId,
  parent,
  manifest,
  enabled,
}) {
  const [result, setResult] = useState(null);
  const parentKey = JSON.stringify(parent);
  useEffect(() => {
    setResult(null);
    if (!enabled) return undefined;
    const ids = [...new Set(manifest.segments.map((s) => s.occurrence_id))];
    if (ids.length === 0 || ids.length > 16) return undefined;
    const controller = new AbortController(),
      signal = controller.signal;
    async function run() {
      const grants = await resolveAttachments(
        projectId,
        ids.map((occurrence_id) => ({
          occurrence_id,
          parent: JSON.parse(parentKey),
          representation: "peaks",
        })),
        signal,
      );
      if (grants.some((g) => g.status !== "ready")) return;
      const byId = new Map();
      let index = 0;
      async function worker() {
        while (index < grants.length && !signal.aborted) {
          const g = grants[index++];
          byId.set(g.occurrence_id, await readPeaks(g.gateway_path, signal));
        }
      }
      await Promise.all([worker(), worker()]);
      if (!signal.aborted)
        setResult({
          manifest,
          parentKey,
          projectId,
          bars: peakOverview(manifest, byId),
        });
    }
    run().catch(() => controller.abort());
    return () => controller.abort();
  }, [projectId, parentKey, manifest, enabled]);
  return enabled &&
    result?.manifest === manifest &&
    result.parentKey === parentKey &&
    result.projectId === projectId
    ? result.bars
    : null;
}
