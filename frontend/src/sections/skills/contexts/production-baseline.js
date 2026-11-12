/** The version the application serves in production, for use as the default baseline of a
 * matched run. It is the `production` label when the context has one; otherwise it is the
 * context's one live runtime binding (an exact version, or the version its label points
 * at). With several live runtime bindings there is no single production, so there is no
 * default and the reviewer chooses. The application-verification target (and its per-run
 * verification bindings) are runtime bindings too, but they are what is being tested, not what
 * production serves, so they never count as the production baseline. */
const VERIFICATION_PROFILES = ['native-application', 'native-verification'];
export function productionBaseline(labels = [], bindings = []) {
  const labelled = labels.find((label) => label.name === 'production')?.version;
  if (labelled) return labelled;
  const live = bindings.filter((binding) => !binding.retiredAt && binding.kind === 'runtime' && !VERIFICATION_PROFILES.includes(binding.profile));
  if (live.length !== 1) return undefined;
  const { selector } = live[0];
  return selector.version ?? labels.find((label) => label.name === selector.label)?.version;
}
