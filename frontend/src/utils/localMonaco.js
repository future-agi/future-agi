import { loader } from "@monaco-editor/react";

// Called before React mounts any editor. Vite's localMonacoPlugin emits the
// installed editor's AMD loader, language bundles, worker and codicon font here.
// No default jsDelivr fallback, including for lazy editor routes.
export function configureLocalMonaco() {
  const base = new URL(import.meta.env.BASE_URL, window.location.origin);
  loader.config({ paths: { vs: new URL("assets/monaco/vs", base).href } });
}
