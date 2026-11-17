import React, { useState, useEffect, useRef, useCallback } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  Box,
  Typography,
  Checkbox,
  FormControlLabel,
  Button,
  Alert,
  CircularProgress,
  Link,
  Stack,
  Divider,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { alpha, useTheme } from "@mui/material/styles";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";

// ---------------------------------------------------------------------------

const apiErrorMessage = (payload, fallback) => {
  const nestedError = payload?.error;
  return (
    payload?.detail ||
    payload?.message ||
    (typeof nestedError === "string" ? nestedError : nestedError?.message) ||
    payload?.result ||
    fallback
  );
};

export default function OAuthConsent() {
  const theme = useTheme();
  const [searchParams] = useSearchParams();

  // New MCP SDK OAuth flow uses request_id; legacy flow uses client_id + redirect_uri
  const requestId = searchParams.get("request_id");
  const clientId = searchParams.get("client_id");
  const redirectUri = searchParams.get("redirect_uri");
  const responseType = searchParams.get("response_type");
  const scope = searchParams.get("scope") || "";
  const state = searchParams.get("state") || "";

  const isNewFlow = !!requestId;

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [consentData, setConsentData] = useState(null);
  const [selectedGroups, setSelectedGroups] = useState({});

  // Fetch consent screen data
  useEffect(() => {
    if (isNewFlow) {
      // New MCP SDK flow: fetch approval info using request_id
      const fetchApproveInfo = async () => {
        try {
          const response = await axios.get(
            `${endpoints.mcp.oauth.approveInfo}?request_id=${encodeURIComponent(requestId)}`,
          );
          if (response.data?.status) {
            const result = response.data.result;
            setConsentData(result);
            const initial = {};
            (result.available_groups || []).forEach((group) => {
              initial[group.slug] = group.checked;
            });
            setSelectedGroups(initial);
          } else {
            setError(
              apiErrorMessage(
                response.data,
                "Failed to load authorization data.",
              ),
            );
          }
        } catch (err) {
          const message = apiErrorMessage(
            err?.response?.data,
            err?.message || "Failed to load authorization data.",
          );
          setError(message);
        } finally {
          setLoading(false);
        }
      };

      fetchApproveInfo();
      return;
    }

    // Legacy flow: validate params and fetch from authorize endpoint
    if (!clientId || !redirectUri) {
      setError("Missing required parameters (client_id, redirect_uri).");
      setLoading(false);
      return;
    }

    if (responseType !== "code") {
      setError("Unsupported response_type. Must be 'code'.");
      setLoading(false);
      return;
    }

    const fetchConsentData = async () => {
      try {
        const params = new URLSearchParams({
          client_id: clientId,
          redirect_uri: redirectUri,
          response_type: responseType,
          scope,
          state,
        });
        const response = await axios.get(
          `${endpoints.mcp.oauth.authorize}?${params.toString()}`,
        );
        if (response.data?.status) {
          const result = response.data.result;
          setConsentData(result);
          // Initialize checkbox state from server response
          const initial = {};
          (result.available_groups || []).forEach((group) => {
            initial[group.slug] = group.checked;
          });
          setSelectedGroups(initial);
        } else {
          setError(
            apiErrorMessage(
              response.data,
              "Failed to load authorization data.",
            ),
          );
        }
      } catch (err) {
        const message = apiErrorMessage(
          err?.response?.data,
          err?.message || "Failed to load authorization data.",
        );
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    fetchConsentData();
  }, [isNewFlow, requestId, clientId, redirectUri, responseType, scope, state]);

  // The permission list scrolls inside the card, and many systems hide
  // scrollbars until someone scrolls. While part of the list is still below,
  // a fade and a short line over its bottom edge say so.
  const scrollRef = useRef(null);
  const [moreBelow, setMoreBelow] = useState(false);

  const updateMoreBelow = useCallback(() => {
    const list = scrollRef.current;
    setMoreBelow(
      Boolean(list) &&
        list.scrollHeight - list.scrollTop - list.clientHeight > 1,
    );
  }, []);

  // Measure again once the list is on screen and whenever the list or its
  // content resizes (a window resize, a phone's toolbars changing the card's
  // height, or text reflowing once fonts load).
  useEffect(() => {
    updateMoreBelow();
    const list = scrollRef.current;
    if (!list) return undefined;
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateMoreBelow);
      return () => window.removeEventListener("resize", updateMoreBelow);
    }
    const observer = new ResizeObserver(updateMoreBelow);
    observer.observe(list);
    Array.from(list.children).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, [updateMoreBelow, loading, error, consentData]);

  const handleToggleGroup = (slug) => {
    setSelectedGroups((prev) => ({ ...prev, [slug]: !prev[slug] }));
  };

  const handleConsent = async (approved) => {
    setSubmitting(true);
    try {
      const groups = approved
        ? Object.entries(selectedGroups)
            .filter(([, checked]) => checked)
            .map(([slug]) => slug)
        : [];

      let response;
      if (isNewFlow) {
        // New MCP SDK flow: POST to approve endpoint with request_id
        response = await axios.post(endpoints.mcp.oauth.approve, {
          request_id: requestId,
          approved,
          selected_groups: groups,
        });
      } else {
        // Legacy flow
        response = await axios.post(endpoints.mcp.oauth.consent, {
          client_id: clientId,
          redirect_uri: redirectUri,
          state,
          approved,
          selected_groups: groups,
        });
      }

      const redirectUrl = response.data?.result?.redirect_url;
      if (response.data?.status && redirectUrl) {
        window.location.href = redirectUrl;
      } else {
        setError("Unexpected response from server.");
        setSubmitting(false);
      }
    } catch (err) {
      setError(
        apiErrorMessage(
          err?.response?.data,
          err?.message || "Authorization failed.",
        ),
      );
      setSubmitting(false);
    }
  };

  // Render
  const selectedCount = Object.values(selectedGroups).filter(Boolean).length;
  const totalCount = consentData?.available_groups?.length || 0;

  return (
    <Box
      sx={{
        // dvh, not vh: the page body never scrolls, so on a phone the card
        // must fit the area left by the browser toolbars.
        height: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: theme.palette.background.default,
        p: 2,
      }}
    >
      <Section
        variant="panel"
        data-testid="mcp-consent-card"
        sx={{
          maxWidth: 480,
          width: "100%",
          maxHeight: "90dvh",
          display: "flex",
          flexDirection: "column",
          boxShadow: theme.customShadows?.card || theme.shadows[8],
          borderRadius: 2,
          overflow: "hidden",
          // Section wraps its children in a body box. Make that box the
          // flex column (the header, list and footer below are its children)
          // so the card's height limit reaches the list: the list scrolls
          // and the Deny/Authorize footer stays on screen. Padding goes
          // because each part already sets its own.
          "& > :last-child": {
            display: "flex",
            flexDirection: "column",
            flex: "1 1 auto",
            minHeight: 0,
            p: 0,
          },
        }}
      >
        {/* Fixed header */}
        <Box sx={{ px: 4, pt: 4, pb: 2, flexShrink: 0 }}>
          <Box sx={{ textAlign: "center", mb: 2 }}>
            <Box
              component="img"
              src="/logo/future_agi_text.svg"
              alt="Future AGI"
              sx={{ height: 28, mx: "auto" }}
              onError={(e) => {
                e.target.style.display = "none";
              }}
            />
          </Box>

          {/* Loading state */}
          {loading && (
            <Box sx={{ textAlign: "center", py: 6 }}>
              <CircularProgress size={36} />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
                Loading authorization details...
              </Typography>
            </Box>
          )}

          {/* Error state */}
          {!loading && error && (
            <Stack spacing={2} alignItems="center">
              <Alert severity="error" sx={{ width: "100%" }}>
                {error}
              </Alert>
              <Typography variant="body2" color="text.secondary">
                Please close this window and try again.
              </Typography>
            </Stack>
          )}

          {!loading && !error && consentData && (
            <>
              <Typography variant="h6" align="center" gutterBottom>
                Authorize MCP Connection
              </Typography>
              <Typography variant="body2" color="text.secondary" align="center">
                <strong>{consentData.client_name}</strong> wants to access your
                Future AGI account
              </Typography>
            </>
          )}
        </Box>

        {/* Tool groups: read-only in the editor sign-in flow, ticks in the legacy flow */}
        {!loading && !error && consentData && (
          <>
            <Divider />
            <Box
              data-testid="mcp-consent-scroll"
              ref={scrollRef}
              onScroll={updateMoreBelow}
              sx={{
                flex: "1 1 auto",
                minHeight: 0,
                overflow: "auto",
                px: 4,
                py: 2,
              }}
            >
              {isNewFlow ? (
                // In this flow the editor's tool list comes from the person's
                // MCP Server settings, not from a choice made here, so the page
                // only says where that list is set. The approval still sends the
                // groups the server pre-selected (see handleConsent).
                <Typography
                  variant="body2"
                  color="text.secondary"
                  data-testid="mcp-consent-tools-note"
                >
                  This editor gets the tool groups that are switched on for you
                  in{" "}
                  <Link
                    component={RouterLink}
                    to={paths.dashboard.settings.mcpServer}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {"Settings > MCP Server"}
                  </Link>
                  .
                </Typography>
              ) : (
                <>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                    sx={{
                      mb: 1,
                      display: "block",
                      textTransform: "none",
                      letterSpacing: 0.5,
                    }}
                  >
                    Permissions ({selectedCount} of {totalCount})
                  </Typography>
                  <Stack spacing={0}>
                    {(consentData.available_groups || []).map((group) => (
                      <FormControlLabel
                        key={group.slug}
                        control={
                          <Checkbox
                            checked={!!selectedGroups[group.slug]}
                            onChange={() => handleToggleGroup(group.slug)}
                            size="small"
                          />
                        }
                        label={
                          <Box>
                            <Typography
                              variant="body2"
                              fontWeight={600}
                              lineHeight={1.3}
                            >
                              {group.name}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {group.description}
                            </Typography>
                          </Box>
                        }
                        sx={{
                          alignItems: "flex-start",
                          mx: 0,
                          py: 0.75,
                          borderBottom: `1px solid ${theme.palette.divider}`,
                          "&:last-of-type": { borderBottom: "none" },
                          "& .MuiCheckbox-root": { pt: 0.25 },
                        }}
                      />
                    ))}
                  </Stack>
                </>
              )}
            </Box>

            {/* Over the list's bottom edge while more of it is below. It takes
                no room and lets clicks through, so nothing moves when it goes.
                Screen readers already get the whole list and its count. */}
            {!isNewFlow && moreBelow && (
              <Box sx={{ position: "relative", height: 0, flexShrink: 0 }}>
                <Box
                  data-testid="mcp-consent-more"
                  aria-hidden="true"
                  sx={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    bottom: 0,
                    zIndex: 1,
                    height: 56,
                    display: "flex",
                    alignItems: "flex-end",
                    justifyContent: "center",
                    pb: 1,
                    pointerEvents: "none",
                    background: `linear-gradient(to bottom, ${alpha(
                      theme.palette.background.paper,
                      0,
                    )}, ${theme.palette.background.paper} 65%)`,
                  }}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    fontWeight={600}
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.5,
                    }}
                  >
                    More permissions below
                    <Iconify icon="solar:alt-arrow-down-linear" width={14} />
                  </Typography>
                </Box>
              </Box>
            )}

            {/* Fixed footer */}
            <Divider />
            <Box
              data-testid="mcp-consent-actions"
              sx={{ px: 4, py: 2.5, flexShrink: 0 }}
            >
              <Stack direction="row" spacing={2} justifyContent="flex-end">
                <Button
                  variant="outlined"
                  color="inherit"
                  onClick={() => handleConsent(false)}
                  disabled={submitting}
                >
                  Deny
                </Button>
                <Button
                  variant="contained"
                  onClick={() => handleConsent(true)}
                  disabled={submitting || selectedCount === 0}
                  sx={{ px: 3 }}
                >
                  {submitting ? (
                    <CircularProgress size={20} color="inherit" />
                  ) : (
                    "Authorize"
                  )}
                </Button>
              </Stack>
            </Box>
          </>
        )}
      </Section>
    </Box>
  );
}
