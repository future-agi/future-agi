// §C10 helper (lane X): string / template literals that hold a digit.
/** String / template literal text (comments and `${…}` removed) holding a digit. */
export function digitLiterals(code) {
  let body = code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  let prev;
  do {
    prev = body;
    body = body.replace(/\$\{[^{}]*\}/g, "");
  } while (body !== prev);
  return (
    [...body.matchAll(/`([^`]*)`/g), ...body.matchAll(/"([^"\n]*)"/g)]
      .map((m) => m[1])
      // Metric NAMES (`p50`, `p90`, `p99`) are words, not numbers.
      .map((t) => t.replace(/\bp\d{2}\b/g, ""))
      .filter((t) => /\d/.test(t))
  );
}
