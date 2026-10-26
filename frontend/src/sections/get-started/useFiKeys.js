import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/*
  The workspace's newest enabled key (the list the Keys page shows), for
  snippets that need FI_API_KEY / FI_SECRET_KEY: masked on screen, copied in
  full. Used by Get started and Settings → Developer, so both show the same.
*/
export default function useFiKeys() {
  /* The workspace's newest enabled key — the list the Keys page shows. */
  const { data, isLoading } = useQuery({
    queryKey: ["get-started-keys"],
    queryFn: () =>
      axios.get(endpoints.keys.getKeys, {
        params: {
          current_page_index: 0,
          page_size: 10,
          sort_field: "created_at",
          sort_order: "desc",
        },
      }),
    select: (d) =>
      (d.data?.result?.table || []).find((r) => r.enabled !== false) || null,
  });
  const mask = (v) =>
    v ? `${String(v).slice(0, 4)}••••••••${String(v).slice(-4)}` : null;
  const api = data?.api_key || null;
  const secret = data?.secret_key || null;
  const shown = (masked) =>
    `export FI_API_KEY=${(masked ? mask(api) : api) || "your_api_key"}\nexport FI_SECRET_KEY=${(masked ? mask(secret) : secret) || "your_secret_key"}`;
  return {
    isLoading,
    name: data?.key_name || null,
    has: !!api,
    api,
    secret,
    mask,
    envShown: shown(true),
    envCopy: shown(false),
  };
}
