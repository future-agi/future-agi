// Read-only span references. Do not replace the legacy source_id mutation
// bridge with this format: those APIs have a separate authorization contract.
export const SPAN_REFERENCE_ERROR =
  "The selected span changed or its identity could not be verified. Refresh the list and try again.";

const text = (value) => typeof value === "string" && value.length > 0;
const UINT64_MAX = 18446744073709551615n;

/** Normalize offset timestamps without losing the six-digit CH precision. */
export const canonicalSpanTimestamp = (value) => {
  if (typeof value !== "string") return null;
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/,
  );
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction = "", zone] = match;
  const leap = +year % 4 === 0 && (+year % 100 !== 0 || +year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    +year < 1 ||
    +month < 1 ||
    +month > 12 ||
    +day < 1 ||
    +day > days[+month - 1] ||
    +hour > 23 ||
    +minute > 59 ||
    +second > 59 ||
    (zone !== "Z" && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59))
  )
    return null;
  const instant = new Date(
    `${year}-${month}-${day}T${hour}:${minute}:${second}${zone}`,
  );
  if (!Number.isFinite(instant.getTime())) return null;
  const utc = instant.toISOString().slice(0, 19);
  if (!/^\d{4}-/.test(utc)) return null;
  return `${utc}.${fraction.padEnd(6, "0")}Z`;
};

const alias = (row, canonical, legacy) => {
  if (
    row?.[canonical] != null &&
    row?.[legacy] != null &&
    row[canonical] !== row[legacy]
  )
    return null;
  return row?.[canonical] ?? row?.[legacy];
};

export const getSpanReadReference = (row) => {
  const project_id = alias(row, "project_id", "project");
  const trace_id = alias(row, "trace_id", "trace");
  const span_id = alias(row, "span_id", "id");
  const expected_start_time = canonicalSpanTimestamp(row?.start_time);
  const expected_version = row?._version;
  if (
    ![project_id, trace_id, span_id].every(text) ||
    !expected_start_time ||
    typeof row?.observation_type !== "string" ||
    typeof row?.service_name !== "string" ||
    typeof expected_version !== "string" ||
    !/^(0|[1-9]\d*)$/.test(expected_version) ||
    expected_version.length > 20 ||
    BigInt(expected_version) > UINT64_MAX
  )
    return null;
  const start_hour = `${expected_start_time.slice(0, 13)}:00:00.000000Z`;
  if (
    row?.start_hour != null &&
    canonicalSpanTimestamp(row.start_hour) !== start_hour
  )
    return null;
  return {
    project_id,
    trace_id,
    span_id,
    start_hour,
    observation_type: row.observation_type,
    service_name: row.service_name,
    expected_start_time,
    expected_version,
  };
};

const physicalParts = (reference) => [
  reference.project_id,
  reference.trace_id,
  reference.span_id,
  reference.start_hour,
  reference.observation_type,
  reference.service_name,
];

export const getSpanReadIdentityKey = (row) => {
  const reference = getSpanReadReference(row);
  return reference ? JSON.stringify(physicalParts(reference)) : null;
};

export const getSpanReadCacheKey = (row) => {
  const reference = getSpanReadReference(row);
  return reference
    ? JSON.stringify([
        ...physicalParts(reference),
        reference.expected_start_time,
        reference.expected_version,
      ])
    : null;
};

export const spanReadRequest = (row) => {
  const reference = getSpanReadReference(row);
  if (!reference) throw new Error(SPAN_REFERENCE_ERROR);
  const { span_id, ...params } = reference;
  return { spanId: span_id, params };
};

export const verifySpanReadResponse = (selectedRow, detail) => {
  const selected = getSpanReadCacheKey(selectedRow);
  if (!selected || selected !== getSpanReadCacheKey(detail))
    throw new Error(SPAN_REFERENCE_ERROR);
  return detail;
};
