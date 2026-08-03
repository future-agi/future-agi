

const isProd = import.meta.env?.PROD === true;

const unwrapEnvelope = (raw) => raw?.data ?? raw;

const assertItemsHaveKeys = (items, requiredItemKeys, label) => {
  if (!requiredItemKeys?.length) return;
  items.forEach((item, index) => {
    if (!item || typeof item !== "object") return;
    const missing = requiredItemKeys.filter((key) => !(key in item));
    if (missing.length === 0) return;

    const message =
      `[contract] ${label}: response item #${index} is missing backend ` +
      `field(s) "${missing.join('", "')}". A serializer rename likely dropped ` +
      `them — update the consumer and regenerate the contract.`;

    // Loud in dev/test so a rename fails the suite; degrade (don't crash the
    // page) in production where a thrown select would only worsen the UX.
    if (isProd) {
      // eslint-disable-next-line no-console
      console.error(message);
    } else {
      throw new Error(message);
    }
  });
};


export const selectContractedList = (
  raw,
  { schema, requiredItemKeys, label, fallback = [] },
) => {
  const envelope = unwrapEnvelope(raw);

  if (schema && !isProd) {
    const parsed = schema.safeParse(envelope);
    if (!parsed.success) {
      // eslint-disable-next-line no-console
      console.error(
        `[contract] ${label}: response failed schema validation`,
        parsed.error.issues,
      );
    }
  }

  const list = envelope?.result ?? envelope?.results ?? envelope ?? fallback;
  if (Array.isArray(list)) {
    assertItemsHaveKeys(list, requiredItemKeys, label);
    return list;
  }
  return fallback;
};
