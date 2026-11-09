/**
 * v2.1 g8 (knob:run-prompt:test-preview): the Test panel's cards, in the
 * order the server returned them (grid order), each with its `Row N` label
 * from the response's `rows[i].row_number` (else its position). Pure.
 */
export function testRowsOf(previewData) {
  const result = previewData?.data?.result ?? {};
  const responses = Array.isArray(result.responses) ? result.responses : [];
  const rows = Array.isArray(result.rows) ? result.rows : [];
  return responses.map((response, i) => ({
    response,
    rowLabel: `Row ${rows[i]?.row_number ?? i + 1}`,
  }));
}
