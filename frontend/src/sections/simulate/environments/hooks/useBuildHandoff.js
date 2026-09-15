import { enqueueSnackbar } from "notistack";
import { useBuildEnvironment } from "src/api/simulate-environments/environments";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
import { BUILD_HANDOFF_COPY } from "../environmentOptions";

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
 * Every "Build environment" CTA funnels through here. It redacts the source
 * up front and hands that safe copy to both the (mocked) build mutation — so
 * react-query never retains the raw secrets as `mutation.state.variables` —
 * and the store draft, then confirms with a snackbar. The real build page
 * lands in Phase-2, which will read the draft from the store.
 */
export default function useBuildHandoff() {
  const build = useBuildEnvironment();
  const setDraft = useEnvironmentsStore((s) => s.setDraft);
  return (source) => {
    const safe = redactSource(source);
    build.mutate(safe, {
      onSuccess: () => {
        setDraft(safe);
        enqueueSnackbar(BUILD_HANDOFF_COPY, { variant: "info" });
      },
    });
  };
}
