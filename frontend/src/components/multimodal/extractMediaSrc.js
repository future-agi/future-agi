// Match flat OTel media attribute paths. Source of truth:
// `MessageContentAttributes` in core-backend/futureagi/tracer/utils/otel.py.

export function isImageAttrPath(path) {
  if (typeof path !== "string") return false;
  return /\.(image|image_url\.url)$/i.test(path);
}

export function isAudioAttrPath(path) {
  if (typeof path !== "string") return false;
  return /\.(audio|audio_content\.url)$/i.test(path);
}
