import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import CompareRunsPage from "../CompareRunsPage";
import { ENV } from "./compareFixtures";

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{location.pathname + location.search}</div>
  );
}

// The compare page at `/environments/:envId/compare<search>`, with a probe
// that shows where it navigated. Callers mock the API hooks first.
export function renderComparePage(search = "?runs=r1,r3") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = (
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          `/dashboard/simulate/environments/${ENV.id}/compare${search}`,
        ]}
      >
        <Routes>
          <Route
            path="/dashboard/simulate/environments/:envId/compare"
            element={
              <>
                <CompareRunsPage />
                <LocationProbe />
              </>
            }
          />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(tree);
}

// jsdom's Blob has no text(); read it the old way.
export const blobText = (blob) =>
  new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(blob);
  });
