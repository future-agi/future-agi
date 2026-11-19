import { evidenceKey, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';
import { evidenceOutcome } from '../utils/review-display';

// URL refs are hints, never authority: resolve annotation revisions from current
// authorized options. Private, expired, wrong-candidate and wrong-target results
// are never attached implicitly. A carried result wins over any suggestion.
//
// UXF-17: without a carried result, suggest only the newest settled Application
// test that matches the target, never the weaker Answer check. A private
// carried result is not returned here: the composer pre-ticks it and attaches
// it only after the person confirms sharing it.
export function suggestedEvidence(evidence, packet, carried = [], now = Date.now()) {
  const eligible = evidence.filter((ref) => !evidenceProblems(ref, packet, now).length);
  if (carried.length) return eligible.filter((ref) => carried.includes(evidenceKey(ref))).map(evidenceSelection);
  // Eligibility already requires the run's configuration to match the target.
  const records = eligible.filter((ref) => {
    const outcome = evidenceOutcome(ref);
    return ref.source === 'simulation_native' && ref.status === 'completed' && outcome && outcome.status !== 'unknown';
  });
  if (records.length === 1) return [evidenceSelection(records[0])];
  // Missing timestamps cannot establish which run is the newest.
  if (!records.length || records.some((ref) => !Number.isFinite(Date.parse(ref.completedAt)))) return [];
  return [evidenceSelection([...records].sort((a, b) => Date.parse(b.completedAt) - Date.parse(a.completedAt))[0])];
}

/**
 * The results a publish hands to its release request: the author's own private, settled results
 * for exactly the bytes just published (tested as the saved draft). They are carried as refs only;
 * the composer pre-ticks them and attaches nothing until the author confirms sharing them.
 */
export function publishedDraftResults(evidence, { subjectId, subjectKind, digest }, now = Date.now()) {
  const packet = { subjectId, subjectKind, candidateDigest: digest, target: null };
  return evidence.filter((ref) => ref.visibility === 'private' && ref.candidateDigest === digest
    && evidenceProblems(ref, packet, now).every((reason) => reason.startsWith('Private')));
}
