/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

const theme = createTheme({ palette: palette("light"), typography });

export function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

// Renders `element` at `path` (a route pattern) visited as `at`, with the
// providers a Hub page needs.
export function renderAt(element, { path = "*", at = "/" } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  function Wrapper({ children }) {
    return (
      <HelmetProvider>
        <QueryClientProvider client={client}>
          <ThemeProvider theme={theme}>
            <MemoryRouter initialEntries={[at]}>
              <Routes>
                <Route path={path} element={children} />
              </Routes>
              <LocationProbe />
            </MemoryRouter>
          </ThemeProvider>
        </QueryClientProvider>
      </HelmetProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(element, { wrapper: Wrapper });
}

// Random text, so a test can prove a page shows the rules file's text and
// none of its own.
export function randomRules(base) {
  const word = () => Math.random().toString(36).slice(2, 10);
  const swap = (value) => (typeof value === "string" ? `R-${word()}-${word()}` : value);
  return {
    ...base,
    admission: base.admission.map((item) => ({ ...item, title: swap(item.title), text: swap(item.text) })),
    content: base.content.map((item) => ({ ...item, text: swap(item.text) })),
    checklist: base.checklist.map((item) => ({ ...item, text: swap(item.text) })),
    counts: Object.fromEntries(Object.keys(base.counts).map((k) => [k, swap(base.counts[k])])),
    reviewMeans: base.reviewMeans.map(swap),
    targets: { ...base.targets, review: swap(base.targets.review) },
  };
}

export function rulesTexts(rules) {
  return [
    ...rules.admission.flatMap((item) => [item.title, item.text]),
    ...rules.content.map((item) => item.text),
    ...rules.checklist.map((item) => item.text),
    ...rules.reviewMeans,
    rules.targets.review,
  ];
}
