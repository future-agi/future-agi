// Decoder for the worker's level → channel → bucket signed little-endian pyramid.
export const MAX_PEAK_BYTES = 32768;
const int = Number.isSafeInteger;
export function peakDescriptor(header, length) {
  if (
    !header ||
    header.length > 1024 ||
    !int(length) ||
    length < 4 ||
    length > MAX_PEAK_BYTES
  )
    throw Error("Invalid peaks");
  const d = JSON.parse(header),
    { sample_rate: rate, channels, frame_count: frames, levels } = d;
  if (
    d.v !== 1 ||
    !int(rate) ||
    rate < 8000 ||
    rate > 96000 ||
    ![1, 2].includes(channels) ||
    !int(frames) ||
    frames < 1 ||
    frames > rate * 30 ||
    !Array.isArray(levels) ||
    levels.length !== 3
  )
    throw Error("Invalid peaks");
  let size = 0;
  for (const [i, l] of levels.entries()) {
    if (
      l.frames_per_bucket !== Math.floor(rate / 100) * 10 ** i ||
      l.buckets !== Math.ceil(frames / l.frames_per_bucket)
    )
      throw Error("Invalid peaks");
    size += 4 * channels * l.buckets;
  }
  if (size !== length) throw Error("Invalid peaks");
  return d;
}
export function decodePeaks(d, bytes) {
  const view = new DataView(bytes);
  let offset = 0;
  const pairs = [];
  for (const [i, l] of d.levels.entries())
    for (let c = 0; c < d.channels; c++)
      for (let b = 0; b < l.buckets; b++) {
        const low = view.getInt16(offset, true),
          high = view.getInt16(offset + 2, true);
        offset += 4;
        if (low > high) throw Error("Invalid peaks");
        if (i === 0) {
          const p = pairs[b];
          pairs[b] = p
            ? [Math.min(p[0], low), Math.max(p[1], high)]
            : [low, high];
        }
      }
  if (offset !== bytes.byteLength) throw Error("Invalid peaks");
  return { pairs, frames: d.frame_count, width: d.levels[0].frames_per_bucket };
}
export function peakOverview(manifest, byId, pixels = 512) {
  const bars = Array(pixels).fill(null),
    segments = manifest.segments;
  const parts =
    segments.every((s) => s.occurrence_id === segments[0].occurrence_id) &&
    segments.length > 1
      ? [{ ...segments[0], start_ms: 0, duration_ms: manifest.duration_ms }]
      : segments;
  for (const s of parts) {
    const { pairs, width, frames } = byId.get(s.occurrence_id);
    const at = (frame) =>
      ((s.start_ms + (s.duration_ms * frame) / frames) * pixels) /
      manifest.duration_ms;
    pairs.forEach(([lo, hi], i) => {
      const end = Math.min(
        pixels,
        Math.ceil(at(Math.min((i + 1) * width, frames))),
      );
      for (let x = Math.max(0, Math.floor(at(i * width))); x < end; x++) {
        const old = bars[x];
        bars[x] = old ? [Math.min(old[0], lo), Math.max(old[1], hi)] : [lo, hi];
      }
    });
  }
  return bars;
}
