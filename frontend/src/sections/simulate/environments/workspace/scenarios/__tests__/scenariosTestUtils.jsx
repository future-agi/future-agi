import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { SAMPLE_ROWS } from "src/api/simulate-environments/_fixtures/scenariosFixtures";
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";

// Shared harness for the ScenariosStep tests once the list reads the server
// (fixtures) source: a QueryClientProvider around the tree, plus helpers to
// build the server rows the mocked `listScenarios` serves and the matching
// envState the mutation paths still target by id.

export const TEST_ENV = { ...MOCK_WORLD, id: "job-test" };

// The 20-row sample suite is the canonical server shape.
export const SERVER_ROWS = SAMPLE_ROWS;

// envState.scenarios is still the mutation target, so seed it from the SAME
// rows the server serves (mapped to our shape) — that keeps the displayed row
// ids and the envState ids aligned, which the remove/bulk-delete tests rely on.
export const envStateFor = (rows = SERVER_ROWS) => ({
  scenarios: rows.map(scenarioFromApi),
});

// A larger server suite for pagination: repeat the samples, re-id and renumber
// so ids stay unique and `number` is a clean 1..n sequence. The row id and the
// scenario key differ, as they do on the server (a UUID vs a slug), so a path
// that sends the wrong one fails its test.
export const makeServerRows = (n) =>
  Array.from({ length: n }, (_, i) => {
    const base = SERVER_ROWS[i % SERVER_ROWS.length];
    return { ...base, id: `srv-${i}`, scenario_key: `key-${i}`, number: i + 1 };
  });

export function renderWithClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return render(<Wrapper>{ui}</Wrapper>);
}
