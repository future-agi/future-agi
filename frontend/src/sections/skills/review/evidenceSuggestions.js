import { evidenceKey, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';

// URL refs are hints, never authority: resolve the annotation revision from the
// current options and require the same candidate, target and sharing state.
export function suggestedEvidence(evidence, packet, carried = [], now = Date.now()) {
  const eligible = evidence.filter((ref) => !evidenceProblems(ref, packet, now).length);
  if (carried.length) return eligible.filter((ref) => carried.includes(evidenceKey(ref))).map(evidenceSelection);
  return ['skills_text', 'simulation_native'].flatMap((source) => {
    const records = eligible.filter((ref) => ref.source === source && ref.status === 'completed');
    // Options currently omit completion time and are ordered by UUID, not time.
    // A sole record is unambiguous. Never call an arbitrary UUID the latest run.
    if (records.length === 1) return [evidenceSelection(records[0])];
    if (!records.length || records.some((ref) => !Number.isFinite(Date.parse(ref.completedAt)))) return [];
    return [evidenceSelection([...records].sort((a, b) => Date.parse(b.completedAt) - Date.parse(a.completedAt))[0])];
  });
}
