export function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Number((bytes / 1024).toFixed(1))} KiB`;
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
}

export function formatTokens(tokens = 0) {
  return `≈ ${Number(tokens).toLocaleString()} tokens`;
}

export function shortDigest(digest = '') {
  return digest.replace(/^sha256:/, '').slice(0, 12);
}

const DECIMAL_USD = /^(?:0|[1-9]\d{0,9})(?:\.\d{1,8})?$/;
// Allowance field text: "20.00000000" reads as "20.00". Only trailing zeros are
// dropped, so a stored value with more precision (12.00000001) is shown in full
// and an untouched save never changes the saved limit.
export function allowanceInput(value) {
  if (value == null || value === '') return '';
  const text = String(value);
  if (!DECIMAL_USD.test(text)) return text;
  const [whole, fraction = ''] = text.split('.');
  const kept = fraction.replace(/0+$/, '');
  return kept.length <= 2 ? `${whole}.${kept.padEnd(2, '0')}` : `${whole}.${kept}`;
}

// ---- Money: "$0.24", "under $0.01", "$25" for people; the exact decimal stays in a tooltip or Details ----
const MONEY_TEXT = /^(\d+)(?:\.(\d+))?$/;
/**
 * Rounds on the decimal text, never through floating point ("1.10000000" is $1.10, not $1.11).
 * round: "nearest" for observed estimates, "up" for ceilings and stops (the figure shown never
 * understates what may be spent), "down" for the most a field accepts (the figure shown is
 * always accepted). Returns null for a missing value; callers say "unknown", never "$0".
 */
export function formatUsd(value, { round = 'nearest' } = {}) {
  if (value == null || value === '') return null;
  const text = String(value).trim();
  const match = MONEY_TEXT.exec(text);
  if (!match) return `USD ${text}`;
  const whole = BigInt(match[1]);
  const fraction = (match[2] || '').replace(/0+$/, '');
  if (!fraction) return `$${whole.toLocaleString('en-US')}`;
  const cents = fraction.slice(0, 2).padEnd(2, '0');
  const rest = fraction.slice(2);
  if (whole === 0n && cents === '00') return round === 'down' ? '$0' : 'under $0.01';
  let total = whole * 100n + BigInt(cents);
  if ((round === 'up' && /[1-9]/.test(rest)) || (round === 'nearest' && rest >= '5')) total += 1n;
  return `$${(total / 100n).toLocaleString('en-US')}.${String(total % 100n).padStart(2, '0')}`;
}

// ---- Dates: one vocabulary across Skills (never seconds in primary views) ----
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const localZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
};
const timestampOf = (value) => {
  const time = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(time) ? time : null;
};
// Fixed locale and parts so the text never depends on the browser's locale.
function parts(time, timeZone, withYear) {
  const values = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone, day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(time)).map((part) => [part.type, part.value]));
  return `${values.day} ${values.month}${withYear ? ` ${values.year}` : ''}, ${values.hour}:${values.minute}`;
}
const plural = (count, unit) => `${count} ${unit}${count === 1 ? '' : 's'}`;
function span(ms) {
  if (ms < MINUTE) return null;
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)} min`;
  if (ms < DAY) return plural(Math.floor(ms / HOUR), 'hour');
  return plural(Math.floor(ms / DAY), 'day');
}

/** "12 min ago" within a day, otherwise "14 Nov, 00:48" (year added when it differs). */
export function formatWhen(value, { now = Date.now(), timeZone = localZone() } = {}) {
  const time = timestampOf(value);
  if (time == null) return '';
  const delta = now - time;
  if (Math.abs(delta) < DAY) {
    const amount = span(Math.abs(delta));
    if (!amount) return 'Just now';
    return delta >= 0 ? `${amount} ago` : `In ${amount}`;
  }
  const yearOf = (at) => new Intl.DateTimeFormat('en-GB', { timeZone, year: 'numeric' }).format(new Date(at));
  return parts(time, timeZone, yearOf(time) !== yearOf(now));
}

/** "Expires in 7 days" / "Expired 2 days ago". */
export function formatExpiry(value, { now = Date.now() } = {}) {
  const time = timestampOf(value);
  if (time == null) return '';
  const delta = time - now;
  const amount = span(Math.abs(delta));
  if (delta > 0) return amount ? `Expires in ${amount}` : 'Expires in less than a minute';
  return amount ? `Expired ${amount} ago` : 'Expired just now';
}

/** "in 7 days" / "expired 2 days ago": the expiry under an "Expires" column header. */
export function formatExpiryShort(value, { now = Date.now() } = {}) {
  const time = timestampOf(value);
  if (time == null) return '';
  const delta = time - now;
  const amount = span(Math.abs(delta));
  if (delta > 0) return amount ? `in ${amount}` : 'in less than a minute';
  return amount ? `expired ${amount} ago` : 'expired just now';
}

/** "14 Nov 2026, 00:48 UTC": the exact time for tooltips and Details. */
export function formatUtcFull(value) {
  const time = timestampOf(value);
  return time == null ? '' : `${parts(time, 'UTC', true)} UTC`;
}
