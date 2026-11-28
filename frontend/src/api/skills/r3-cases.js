export const nativeSelectionInput = (value) => ({
  child_selection: value.childSelection.map(({ skillId, expectation }) => ({ skill_id: skillId, expectation })),
  catalog_digest: value.catalogDigest, invocation_mode: value.invocationMode,
});
export const mapNativeSelection = (value) => ({
  childSelection: value.child_selection.map(({ skill_id: skillId, expectation }) => ({ skillId, expectation })),
  catalogDigest: value.catalog_digest, invocationMode: value.invocation_mode,
});
export const humanAnnotationInput = (value) => ({
  source: value.source, source_id: value.sourceId, annotation_revision: value.annotationRevision,
  check_id: value.checkId, attempt_id: value.attemptId, verdict: value.verdict,
  cited_text: value.citedText, comment: value.comment,
});
export const mapHumanAnnotation = (value) => ({
  source: value.source, sourceId: value.source_id, annotationRevision: value.annotation_revision,
  checkId: value.check_id, attemptId: value.attempt_id, verdict: value.verdict,
  citedText: value.cited_text, comment: value.comment,
});
