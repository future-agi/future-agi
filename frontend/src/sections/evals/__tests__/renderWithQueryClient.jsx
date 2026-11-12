import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

// A fresh client for every render: no retries, no cache shared between
// tests and no refetch on focus or reconnect. Pages read through react-query,
// so a test that renders them needs a provider even when every read it makes
// is mocked or disabled; the client itself never issues a request.
export const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: { retry: false },
    },
  });

// Same providers and options as src/utils/test-utils `render` (router, theme,
// `theme` option), plus a QueryClientProvider. `rerender` keeps the same
// client so a rerender updates the tree instead of remounting it.
export const renderWithQueryClient = (
  ui,
  { queryClient = createTestQueryClient(), ...options } = {},
) => {
  const withClient = (node) => (
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>
  );
  const result = render(withClient(ui), options);
  return {
    ...result,
    queryClient,
    rerender: (next) => result.rerender(withClient(next)),
  };
};
