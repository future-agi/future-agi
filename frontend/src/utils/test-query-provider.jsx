import React, { useState } from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Match the app's query context without sharing caches across renders/tests.
// Each suite still supplies its own responses; queries are not disabled here.
export function TestQueryProvider({ children }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

TestQueryProvider.propTypes = { children: PropTypes.node };
