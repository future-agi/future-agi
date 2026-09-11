export function buildConnectorSavePayload(
  form,
  { preserveEmptySecret = false } = {},
) {
  const payload = { ...form };
  if (
    preserveEmptySecret &&
    typeof payload.auth_header_value === "string" &&
    payload.auth_header_value.trim() === ""
  ) {
    delete payload.auth_header_value;
  }
  return payload;
}
