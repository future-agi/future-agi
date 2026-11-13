import { paths } from 'src/routes/paths';

/** A handoff carries immutable identity, not authority or annotation revisions.
 * The composer must match refs against fresh options before attaching evidence.
 */
export function reviewComposerUrl({ kind, subjectId, name, candidateId, number, digest, label, evidence = [], target }) {
  const params = new URLSearchParams({ compose: '1' });
  const fields = { kind, subject: subjectId, name, candidate: candidateId, number, digest, label, target };
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  const refs = evidence.map((ref) => {
    const id = ref.sourceId ?? ref.source_id ?? ref.id;
    return ref.source && id ? `${ref.source}:${id}` : null;
  }).filter(Boolean);
  if (refs.length) params.set('evidence', refs.join(','));
  return `${paths.dashboard.skills.reviews}?${params.toString()}`;
}
