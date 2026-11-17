// Time-zone arithmetic for schedules (notify DESIGN §2.2), on
// Intl.DateTimeFormat only (the frontend has date-fns 2 and no tz library).
// Pure: every function takes the instant it needs; nothing reads the clock.
const MIN = 60_000;
const DAY = 86_400_000;

const formatters = new Map();
function partsFormatter(tz) {
  if (!formatters.has(tz))
    formatters.set(
      tz,
      new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        hourCycle: "h23",
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
        second: "numeric",
      }),
    );
  return formatters.get(tz);
}

/** Wall-clock fields of an instant in a zone: {y, m, d, h, mi, s}. */
export function wallOf(ms, tz) {
  const out = {};
  partsFormatter(tz)
    .formatToParts(new Date(ms))
    .forEach((p) => {
      if (p.type !== "literal") out[p.type] = Number(p.value);
    });
  return {
    y: out.year,
    m: out.month,
    d: out.day,
    h: out.hour === 24 ? 0 : out.hour,
    mi: out.minute,
    s: out.second,
  };
}

/** The zone's offset from UTC at an instant, in ms (IST = +19,800,000). */
export function offsetMs(ms, tz) {
  const w = wallOf(ms, tz);
  const wall = Date.UTC(w.y, w.m - 1, w.d, w.h, w.mi, w.s);
  return wall - Math.floor(ms / 1000) * 1000;
}

/**
 * The UTC instant of a local wall time. A time that happens twice (the
 * fall-back hour) resolves to the first; a time that doesn't exist (the
 * spring-forward gap) resolves to the next valid minute, the end of the gap,
 * with `shifted: true` (DESIGN §2.2).
 * @returns {{ms: number, shifted: boolean}}
 */
export function localToUtc(y, m, d, h, mi, tz) {
  const guess = Date.UTC(y, m - 1, d, h, mi);
  const offs = [
    ...new Set([guess - DAY, guess, guess + DAY].map((t) => offsetMs(t, tz))),
  ];
  const hits = offs
    .map((o) => guess - o)
    .filter((t) => t + offsetMs(t, tz) === guess);
  if (hits.length) return { ms: Math.min(...hits), shifted: false };
  // In a gap: the first minute whose wall time is past `guess`.
  let lo = guess - Math.max(...offs);
  let hi = guess - Math.min(...offs);
  if (lo > hi) [lo, hi] = [hi, lo];
  while (hi - lo > MIN) {
    const mid = lo + Math.floor((hi - lo) / 2 / MIN) * MIN;
    if (mid + offsetMs(mid, tz) > guess) hi = mid;
    else lo = mid;
  }
  return { ms: hi, shifted: true };
}

// Zones renamed in the IANA database (ICU still answers some old names,
// e.g. Asia/Kolkata resolves to Asia/Calcutta) and common aliases.
const ZONE_ALIASES = Object.freeze({
  "asia/calcutta": "Asia/Kolkata",
  "asia/saigon": "Asia/Ho_Chi_Minh",
  "asia/katmandu": "Asia/Kathmandu",
  "asia/rangoon": "Asia/Yangon",
  "asia/dacca": "Asia/Dhaka",
  "asia/thimbu": "Asia/Thimphu",
  "asia/ujung_pandang": "Asia/Makassar",
  "asia/ulan_bator": "Asia/Ulaanbaatar",
  "europe/kiev": "Europe/Kyiv",
  "europe/uzhgorod": "Europe/Kyiv",
  "europe/zaporozhye": "Europe/Kyiv",
  "atlantic/faeroe": "Atlantic/Faroe",
  "america/godthab": "America/Nuuk",
  "america/buenos_aires": "America/Argentina/Buenos_Aires",
  "america/indianapolis": "America/Indiana/Indianapolis",
  "pacific/truk": "Pacific/Chuuk",
  "pacific/ponape": "Pacific/Pohnpei",
  "us/eastern": "America/New_York",
  "us/central": "America/Chicago",
  "us/mountain": "America/Denver",
  "us/pacific": "America/Los_Angeles",
  "us/alaska": "America/Anchorage",
  "us/hawaii": "Pacific/Honolulu",
  utc: "UTC",
  "etc/utc": "UTC",
  "etc/uct": "UTC",
  "etc/gmt": "UTC",
  "etc/zulu": "UTC",
  "etc/universal": "UTC",
  gmt: "UTC",
  uct: "UTC",
  zulu: "UTC",
  universal: "UTC",
});

/**
 * A zone name in its current IANA spelling (`Asia/Calcutta` →
 * `Asia/Kolkata`, `us/eastern` → `America/New_York`, `Etc/UTC` → `UTC`), or
 * null when it isn't a zone. Offsets such as `+05:30` are refused even
 * though Intl takes them: a repeat keeps an IANA zone (DESIGN §2.2), which
 * the Python scheduler's zoneinfo also reads; `Etc/GMT-5` stays a zone.
 */
export function normalizeZone(tz) {
  const raw = String(tz ?? "").trim();
  if (!raw || /^[+-]/.test(raw)) return null;
  const alias = ZONE_ALIASES[raw.toLowerCase()];
  if (alias) return alias;
  let resolved;
  try {
    resolved = new Intl.DateTimeFormat("en-US", {
      timeZone: raw,
    }).resolvedOptions().timeZone;
  } catch {
    return null;
  }
  return ZONE_ALIASES[resolved.toLowerCase()] ?? resolved;
}

const pad2 = (n) => String(n).padStart(2, "0");

/** `UTC+05:30` / `UTC-04:00` / `UTC` for an offset in ms. */
export function utcOffsetLabel(off) {
  if (!off) return "UTC";
  const mins = Math.round(Math.abs(off) / MIN);
  return `UTC${off < 0 ? "-" : "+"}${pad2(Math.floor(mins / 60))}:${pad2(mins % 60)}`;
}

/**
 * The zone's short name at an instant: `IST`, `EDT`, `CEST`, `BST`, `UTC`;
 * `UTC+09:00` where no locale has a letter abbreviation.
 */
export function zoneAbbr(tz, atMs) {
  if (normalizeZone(tz) === "UTC") return "UTC";
  for (const locale of ["en-US", "en-GB", "en-IN"]) {
    const name = new Intl.DateTimeFormat(locale, {
      timeZone: tz,
      timeZoneName: "short",
    })
      .formatToParts(new Date(atMs))
      .find((p) => p.type === "timeZoneName")?.value;
    if (name && /^[A-Z]{2,5}$/.test(name)) return name;
  }
  return utcOffsetLabel(offsetMs(atMs, tz));
}

/** True when the zone keeps one offset all year around `atMs`. */
export function fixedOffset(tz, atMs) {
  const o = offsetMs(atMs, tz);
  return [-183, -91, 91, 183].every(
    (days) => offsetMs(atMs + days * DAY, tz) === o,
  );
}
