import React, { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Card,
  Typography,
  Checkbox,
  FormControlLabel,
  Button,
  Alert,
  CircularProgress,
  Stack,
  Divider,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";

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

      const redirectUrl =
        response.data?.result?.redirect_url;
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
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: theme.palette.background.default,
        p: 2,
      }}
    >
      <Card
        sx={{
          maxWidth: 480,
          width: "100%",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: theme.customShadows?.card || theme.shadows[8],
          borderRadius: 2,
          overflow: "hidden",
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

        {/* Scrollable permissions */}
        {!loading && !error && consentData && (
          <>
            <Divider />
            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                px: 4,
                py: 2,
              }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                fontWeight={600}
                sx={{
                  mb: 1,
                  display: "block",
                  textTransform: "uppercase",
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
                        <Typography variant="caption" color="text.secondary">
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
            </Box>

            {/* Fixed footer */}
            <Divider />
            <Box sx={{ px: 4, py: 2.5, flexShrink: 0 }}>
              <Alert
                severity="info"
                variant="outlined"
                sx={{ mb: 2, py: 0, "& .MuiAlert-message": { py: 0.75 } }}
              >
                <Typography variant="caption">
                  You can revoke access anytime in Settings.
                </Typography>
              </Alert>
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
                  sx={{
                    bgcolor: "#7C3AED",
                    color: "#fff",
                    fontWeight: 600,
                    px: 3,
                    "&:hover": { bgcolor: "#6D28D9" },
                    "&.Mui-disabled": {
                      bgcolor: "action.disabledBackground",
                    },
                  }}
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
      </Card>
    </Box>
  );
}
