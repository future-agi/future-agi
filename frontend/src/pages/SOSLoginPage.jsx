import { Typography } from "@mui/material";
import React, { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthRedirectParams } from "src/auth/auth-url-intake";
import { ShowComponent } from "src/components/show";

const SOSLoginPage = () => {
  const searchParams = useAuthRedirectParams();

  const access = searchParams.get("access");
  const refresh = searchParams.get("refresh");
  const navigate = useNavigate();
  const refreshTimeoutRef = useRef(null);

  useEffect(() => {
    if (!access || !refresh) {
      return;
    }
    // Set items in localStorage when user visits this route
    localStorage.setItem("sosMode", "true");
    localStorage.setItem("accessToken", access);
    localStorage.setItem("refreshToken", refresh);

    // Refresh the page after setting localStorage
    refreshTimeoutRef.current = setTimeout(() => {
      searchParams.clear();
      navigate("/dashboard/develop");
    }, 1000); // Small delay to show the loading message

    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
    };
  }, [access, refresh, navigate, searchParams]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
      }}
    >
      <h2>🚨 SOS Mode Activating...</h2>
      <p>Setting up emergency access...</p>
      <div
        style={{
          marginTop: "20px",
          padding: "10px",
          backgroundColor: "var(--bg-default)",
          borderRadius: "5px",
          fontSize: "12px",
        }}
      >
        <ShowComponent condition={Boolean(!access || !refresh)}>
          <Typography typography="m2">
            Did not receive access token or refresh token
          </Typography>
        </ShowComponent>
        <ShowComponent condition={Boolean(access && refresh)}>
          <Typography typography="s1">
            Page will refresh automatically...
          </Typography>
        </ShowComponent>
      </div>
    </div>
  );
};

export default SOSLoginPage;
