import { occurrenceFromUri, referenceSchema, uuid } from "./contract";
const carrier = "fi.attachment.refs";
const uriLike = (s) => typeof s === "string" && /^\s*fi-attachment/i.test(s);
function decoded(value) {
  if (
    typeof value !== "string" ||
    value.length > 16384 ||
    !/^\s*(?:\[|{)/.test(value) ||
    new TextEncoder().encode(value).length > 16384
  )
    return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
export function reservedClipValue(path, value) {
  const parsed = decoded(value);
  return (
    path[0] === carrier ||
    uriLike(value) ||
    uriLike(parsed?.uri) ||
    (typeof value === "string" &&
      (/^\s*\{\s*"uri"\s*:/.test(value) ||
        (/^\s*(?:\[|{)/.test(value) && /fi-attachment/i.test(value)) ||
        uuid.safeParse(value).success)) ||
    path.join(".") === "turn.audio"
  );
}
export function clipPlacement(attributes, items) {
  const audio = new Map(
    items
      .filter(
        (x) =>
          x.reference.modality === "audio" &&
          x.reference.purpose !== "conversation_recording",
      )
      .map((x) => [x.reference.occurrence_id, x]),
  );
  const paths = new Map(),
    placed = new Set(),
    fallback = [];
  function place(path, id) {
    if (!audio.has(id) || placed.has(id)) return;
    const k = JSON.stringify(path);
    paths.set(k, [...(paths.get(k) || []), audio.get(id)]);
    placed.add(id);
  }
  let visited = 0;
  function visit(value, path) {
    if (++visited > 16000 || path.length > 32) return;
    const candidate = decoded(value),
      id = occurrenceFromUri(
        typeof candidate === "string" ? candidate : candidate?.uri,
      );
    if (id) place(path, id);
    else if (value && typeof value === "object")
      for (const [k, v] of Object.entries(value)) visit(v, [...path, k]);
  }
  for (const [key, val] of Object.entries(attributes))
    if (key !== carrier) visit(val, [key]);
  const refs = decoded(attributes[carrier]);
  const encoded = attributes[carrier];
  const validRefs =
    Array.isArray(refs) &&
    refs.length <= 64 &&
    new TextEncoder().encode(
      typeof encoded === "string" ? encoded : JSON.stringify(refs),
    ).length <= 16384 &&
    refs.every((ref) => referenceSchema.safeParse(ref).success) &&
    new Set(refs.map((ref) => ref.occurrence_id)).size === refs.length;
  if (validRefs)
    for (const [i, ref] of refs.entries()) {
      const actual = audio.get(ref.occurrence_id)?.reference;
      if (actual && Object.entries(ref).every(([k, v]) => actual[k] === v))
        place([carrier, String(i)], ref.occurrence_id);
    }
  for (const [id, item] of audio)
    if (!placed.has(id) && item.reference.purpose === "turn_recording") {
      fallback.push(item);
      placed.add(id);
    }
  return { paths, placed, fallback, refs: validRefs ? refs : null };
}
