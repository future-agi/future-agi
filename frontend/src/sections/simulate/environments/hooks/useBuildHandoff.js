import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";

/**
 * Strip raw secrets before anything is persisted. `apiKey` and `envText` carry
 * plaintext credentials; the draft is a Phase-2 placeholder that never needs
 * them, and keeping them out avoids leaking into the zustand store / devtools.
 * `secretFiles` is already a list of `{name,size,secret_ref}` — no contents.
 */
export function redactSource(source) {
  const safe = { ...(source ?? {}) };
  delete safe.apiKey;
  delete safe.envText;
  return safe;
}

/**
 * Every "Build environment" CTA funnels through here. It redacts the source up
 * front, hands that safe copy to the store draft, then routes to the build
 * page. The Phase-2 build page reads the draft from the store; it mints the env
 * id when the audit is accepted.
 */
export default function useBuildHandoff() {
  const navigate = useNavigate();
  const setDraft = useEnvironmentsStore((s) => s.setDraft);
  return (source) => {
    setDraft(redactSource(source));
    navigate(paths.dashboard.simulate.environments.build);
  };
}
