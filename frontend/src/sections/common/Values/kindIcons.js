// Iconify icons per value kind (sniff.js kinds + JSON kinds), for PathInput
// options, FieldSelection, the Schema drawer and media chips.
export const KIND_ICON = {
  empty: "mdi:circle-outline",
  null: "mdi:circle-off-outline",
  text: "mdi:format-text",
  string: "mdi:format-text",
  markdown: "mdi:language-markdown-outline",
  yaml_text: "mdi:code-braces",
  number: "mdi:pound",
  integer: "mdi:pound",
  float: "mdi:pound",
  boolean: "mdi:toggle-switch-outline",
  enum: "mdi:format-list-bulleted-type",
  datetime: "mdi:calendar-clock-outline",
  object: "mdi:code-json",
  json: "mdi:code-json",
  array: "mdi:code-brackets",
  chat: "mdi:message-text-outline",
  conversation: "mdi:message-text-outline",
  image: "mdi:image-outline",
  images: "mdi:image-multiple-outline",
  audio: "mdi:waveform",
  video: "mdi:filmstrip",
  document: "mdi:file-document-outline",
  ref: "mdi:transit-connection-variant",
  trace: "mdi:transit-connection-variant",
  embedding: "mdi:vector-polyline",
};

/** Icon for a kind (falls back to the text icon). */
export const kindIcon = (kind) => KIND_ICON[kind] ?? KIND_ICON.text;
