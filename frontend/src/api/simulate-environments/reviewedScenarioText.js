const marker = "\n\nReviewed source expectations: ";

// Older saved suites carry the verifier payload after the human-authored test.
export function visibleScenarioTest(value) {
  if (typeof value !== "string") return value;
  const index = value.lastIndexOf(marker);
  if (index < 0) return value;
  try {
    const evidence = JSON.parse(value.slice(index + marker.length));
    if (
      Array.isArray(evidence?.citations) &&
      Array.isArray(evidence.starting_facts)
    )
      return value.slice(0, index);
  } catch {
    // A scenario author can use the same words in ordinary text.
  }
  return value;
}
