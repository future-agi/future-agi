// Display conformance only, NEVER money evidence. Typed specialization of
// frontend/src/sections/common/format/money.js decimal + formatExactMoney
// with grouping=true and default padding/prefix/missing, as called by
// frontend/src/sections/gateway/utils/formatters.js at
// f84ef75afc059d424ccbb736f3e40702f1e08571. No amount is converted through Number.
export type Amount = string | number | null | undefined;
export const EMPTY = '—';
const DECIMAL = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i;

function decimal(value: Amount) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  if (typeof value === 'number' && !Number.isFinite(value)) return null;
  const source = String(value).trim();
  const match = DECIMAL.exec(source);
  if (!match) return null;
  const whole = match[2] ?? '0';
  const fraction = match[3] ?? match[4] ?? '';
  const digits = whole + fraction;
  const negative = match[1] === '-' && /[1-9]/.test(digits);
  const exponent = Number(match[5] ?? 0);
  const point = whole.length + exponent;
  if (!Number.isSafeInteger(point) || Math.abs(point) > 10000) {
    return /[1-9]/.test(digits)
      ? { negative, scientific: source.replace(/^[+-]/, ''), whole: '', fraction: '' }
      : { negative: false, whole: '0', fraction: '' };
  }
  let integer: string;
  let tail: string;
  if (point <= 0) {
    integer = '0';
    tail = '0'.repeat(-point) + digits;
  } else if (point >= digits.length) {
    integer = digits + '0'.repeat(point - digits.length);
    tail = '';
  } else {
    integer = digits.slice(0, point);
    tail = digits.slice(point);
  }
  return {
    negative,
    whole: integer.replace(/^0+(?=\d)/, ''),
    fraction: tail.replace(/0+$/, ''),
  };
}

/** Exact decimal-string digits with grouping and at least two fractional places. */
export function formatExactCost(value: Amount) {
  const parts = decimal(value);
  if (!parts) return EMPTY;
  const sign = parts.negative ? '-' : '';
  if (parts.scientific) return `${sign}$${parts.scientific}`;
  const whole = parts.whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const fraction = parts.fraction.padEnd(2, '0');
  return `${sign}$${whole}.${fraction}`;
}

export function formatCostCell(value: Amount) {
  return formatExactCost(value);
}

export function formatBalance(value: Amount) {
  return formatExactCost(value);
}
