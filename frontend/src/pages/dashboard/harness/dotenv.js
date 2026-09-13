export function parseDotEnv(text) {
  const values = {};
  const lines = String(text || "")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/);
  lines.forEach((original, index) => {
    let line = original.trim();
    if (!line || line.startsWith("#")) return;
    if (line.startsWith("export ")) line = line.slice(7).trimStart();
    const separator = line.indexOf("=");
    if (separator < 1)
      throw new Error(`Invalid .env assignment on line ${index + 1}`);
    const name = line.slice(0, separator).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name))
      throw new Error(`Invalid environment name on line ${index + 1}: ${name}`);
    let value = line.slice(separator + 1).trim();
    if (value.startsWith('"')) {
      if (!value.endsWith('"'))
        throw new Error(`Unclosed double quote on line ${index + 1}`);
      value = value
        .slice(1, -1)
        .replaceAll("\\n", "\n")
        .replaceAll("\\r", "\r")
        .replaceAll("\\t", "\t")
        .replaceAll('\\"', '"')
        .replaceAll("\\\\", "\\");
    } else if (value.startsWith("'")) {
      if (!value.endsWith("'"))
        throw new Error(`Unclosed single quote on line ${index + 1}`);
      value = value.slice(1, -1);
    } else {
      value = value.replace(/\s+#.*$/, "").trim();
    }
    values[name] = value;
  });
  if (Object.keys(values).length > 256)
    throw new Error("A maximum of 256 environment variables is supported");
  return values;
}
