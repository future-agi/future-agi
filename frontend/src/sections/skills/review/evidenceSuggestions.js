import { evidenceKey, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';
import { evidenceOutcome } from '../utils/review-display';

// URL refs are hints, never authority: resolve annotation revisions from current
// authorized options. Private, expired, wrong-candidate and wrong-target results
// are never attached implicitly. A carried result wins over any suggestion.
export function suggestedEvidence(evidence, packet, carried = [], now = Date.now()) {
  const eligible = evidence.filter((ref) => !evidenceProblems(ref, packet, now).length);
  if (carried.length) return eligible.filter((ref) => carried.includes(evidenceKey(ref))).map(evidenceSelection);
  return ['simulation_native', 'skills_text'].flatMap((source) => {
    const records = eligible.filter((ref) => {
      const outcome = evidenceOutcome(ref);
      return ref.source === source && ref.status === 'completed' && outcome && outcome.status !== 'unknown';
    });
    const passed = records.filter((ref) => ['pass', 'passed'].includes(evidenceOutcome(ref)?.status));
    const ranked = passed.length ? passed : records;
    if (ranked.length === 1) return [evidenceSelection(ranked[0])];
    // Missing timestamps cannot establish the latest among equal outcomes.
    if (!ranked.length || ranked.some((ref) => !Number.isFinite(Date.parse(ref.completedAt)))) return [];
    return [evidenceSelection([...ranked].sort((a, b) => Date.parse(b.completedAt) - Date.parse(a.completedAt))[0])];
  });
}
