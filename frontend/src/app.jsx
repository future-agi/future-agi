import React, { useEffect } from "react";
import { useLocation } from "react-router-dom";
import "src/global.css";
import { isChunkError } from "src/utils/lazyWithRetry";

// ----------------------------------------------------------------------

import Router from "src/routes/sections";

import ThemeProvider from "src/theme";

import { useScrollToTop } from "src/hooks/use-scroll-to-top";

import ProgressBar from "src/components/progress-bar";
import { MotionLazy } from "src/components/animate/motion-lazy";
import { SettingsDrawer, SettingsProvider } from "src/components/settings";

import { AuthProvider } from "src/auth/context/jwt";
import { WorkspaceProvider } from "src/contexts/WorkspaceContext";
import { OrganizationProvider } from "src/contexts/OrganizationContext";
import { LocalizationProvider } from "./locales";
import { enqueueSnackbar, SnackbarProvider } from "./components/snackbar";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { BrowserAgent } from "@newrelic/browser-agent/loaders/browser-agent";
import { devTracing, prodTracing } from "./newrelic";
import { CURRENT_ENVIRONMENT } from "./config-global";
import { ErrorBoundary } from "react-error-boundary";
import ErrorFallback from "./pages/ErrorFallback";
import UploadLimitNotification from "./components/rate-limit-modal/RateLimitModal";
import { WebSocketProvider } from "./components/websocket/use-socket";
import { RESPONSE_CODES } from "./utils/constants";
import { registerGlobalCleanup } from "./utils/memory-management";
import * as Sentry from "@sentry/react";
import logger from "./utils/logger";
import { useGoogleReCaptcha } from "react-google-recaptcha-v3";
import { setRecaptchaExecutor } from "./utils/recaptchaService";
import { AudioPlaybackProvider } from "./components/custom-audio/context-provider/AudioPlaybackContext";

// ----------------------------------------------------------------------
const _extractParts = (result) => {
  if (result == null || result === "") return "";
  if (typeof result === "string") return result;
  if (Array.isArray(result)) {
    return [...new Set(result.map(_extractParts).filter(Boolean))].join(", ");
  }
  if (typeof result === "object") {
    if (result.details && typeof result.details === "object") {
      return _extractParts(result.details);
    }
    return [
      ...new Set(Object.values(result).map(_extractParts).filter(Boolean)),
    ].join(", ");
  }
  return String(result);
};

const extractErrorMessage = (result) => _extractParts(result) || "Something went wrong";

const handleError = (error, variable, context, mutation) => {
  if (error?.statusCode == RESPONSE_CODES.LIMIT_REACHED) return;
  if (
    mutation?.options?.meta?.errorHandled ||
    variable?.options?.meta?.errorHandled
  )
    return;
  if (error?.result) {
    enqueueSnackbar(extractErrorMessage(error.result), {
      variant: "error",
    });
  }
};
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleError,
  }),
  mutationCache: new MutationCache({
    onError: handleError,
  }),
  defaultOptions: {
    queries: {
      // Data stays fresh for 30s — no refetch on remount/focus within this window
      staleTime: 30 * 1000,
      // Keep unused data in cache for 5 min (default)
      gcTime: 5 * 60 * 1000,
      // Don't refetch when browser tab regains focus
      refetchOnWindowFocus: false,
      // Retry network/timeout/5xx failures (survives an intermittently
      // stalling backend) but not 4xx, which won't change on retry.
      retry: (failureCount, error) => {
        // The axios interceptor flattens rejections to { ...errData, statusCode }
        // with no `.response`, so read the flattened statusCode (falling back to
        // response.status for any error that bypassed the interceptor).
        const status = error?.statusCode ?? error?.response?.status;
        if (status && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    },
  },
});

// Clear chunk reload flag on successful app load
sessionStorage.removeItem("chunk_reload_attempted");

// Initialize the BrowserAgent
if (CURRENT_ENVIRONMENT === "production") new BrowserAgent(prodTracing);
if (CURRENT_ENVIRONMENT === "dev") new BrowserAgent(devTracing);

export default function App() {
  useScrollToTop();
  const location = useLocation();

  // Register global memory cleanup
  useEffect(() => {
    return registerGlobalCleanup();
  }, []);

  useEffect(() => {
    if (window.Appcues) {
      window.Appcues.page();
    }
  }, [location.pathname]);

  // Clear chunk reload flag on successful mount so future deploys can trigger a reload
  useEffect(() => {
    sessionStorage.removeItem("chunk_reload_attempted");
  }, []);

  const logError = (error, info) => {
    // Chunk errors after a deploy — silently reload once instead of showing error page
    if (
      isChunkError(error) &&
      !sessionStorage.getItem("chunk_reload_attempted")
    ) {
      sessionStorage.setItem("chunk_reload_attempted", "1");
      window.location.reload();
      return;
    }

    Sentry.captureException(error, {
      contexts: {
        react: {
          componentStack: info.componentStack,
        },
      },
    });
    logger.error("Error:", error);
  };

  // setting up recaptcha
  const { executeRecaptcha } = useGoogleReCaptcha();

  useEffect(() => {
    if (executeRecaptcha) {
      setRecaptchaExecutor(executeRecaptcha);
    }
  }, [executeRecaptcha]);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <OrganizationProvider>
          <WorkspaceProvider>
            <WebSocketProvider>
              <LocalizationProvider>
                <SettingsProvider
                  defaultSettings={{
                    themeMode: "system", // 'light' | 'dark' | 'system'
                    themeDirection: "ltr", //  'rtl' | 'ltr'
                    themeContrast: "default", // 'default' | 'bold'
                    themeLayout: "vertical", // 'vertical' | 'horizontal' | 'mini'
                    themeColorPresets: "purple", // 'default' | 'cyan' | 'purple' | 'blue' | 'orange' | 'red'
                    themeStretch: false,
                  }}
                >
                  <ThemeProvider>
                    <MotionLazy>
                      <SnackbarProvider>
                        <AudioPlaybackProvider>
                          <SettingsDrawer />
                          <ProgressBar />
                          <ErrorBoundary
                            FallbackComponent={({
                              error,
                              resetErrorBoundary,
                            }) => {
                              // Chunk errors trigger a silent reload in onError —
                              // render nothing while the page reloads
                              if (isChunkError(error)) {
                                return null;
                              }
                              return (
                                <ErrorFallback
                                  error={error}
                                  resetErrorBoundary={() => {
                                    resetErrorBoundary();
                                    window.location.reload();
                                  }}
                                />
                              );
                            }}
                            onError={logError}
                          >
                            <Router />
                            <UploadLimitNotification />
                          </ErrorBoundary>
                        </AudioPlaybackProvider>
                      </SnackbarProvider>
                    </MotionLazy>
                  </ThemeProvider>
                </SettingsProvider>
              </LocalizationProvider>
            </WebSocketProvider>
          </WorkspaceProvider>
        </OrganizationProvider>
      </AuthProvider>
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  );
}
