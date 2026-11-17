// Seeded sampling. Nothing here reads Math.random or the clock, so identical
// inputs always produce identical results.

// FNV-1a 32-bit hash of a string with a murmur3 finalizer, so keys that
// differ only in a trailing digit ("row|57", "row|58") spread evenly.
export function hash32(input) {
  const text = String(input);
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b) >>> 0;
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h ^= h >>> 16;
  return h >>> 0;
}

export const hashHex = (input) => hash32(input).toString(16).padStart(8, "0");

// mulberry32: tiny, fast, good enough for fixtures.
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// rng("run", runId, rowId, trial) -> a generator seeded from the joined parts.
export const rng = (...parts) => mulberry32(hash32(parts.join("|")));

// One deterministic uniform [0,1) for a key.
export const unit = (...parts) => rng(...parts)();

export function pick(random, items) {
  return items[Math.floor(random() * items.length) % items.length];
}

export function intBetween(random, min, max) {
  return min + Math.floor(random() * (max - min + 1));
}

export function between(random, min, max) {
  return min + random() * (max - min);
}

// Deterministic shuffle (Fisher-Yates) of a copy.
export function shuffled(items, ...seedParts) {
  const random = rng("shuffle", ...seedParts);
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

// Items ordered by a stable hash of (salt, key): a reproducible "random"
// ranking used to pick exactly k rows for a designed count.
export function rankByHash(items, salt, keyOf = (x) => x) {
  return items
    .map((item) => ({ item, h: hash32(`${salt}|${keyOf(item)}`) }))
    .sort((a, b) => a.h - b.h)
    .map(({ item }) => item);
}

export const round = (value, digits = 2) => {
  const f = 10 ** digits;
  return Math.round(value * f) / f;
};
