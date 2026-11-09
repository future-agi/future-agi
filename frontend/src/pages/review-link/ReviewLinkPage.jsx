import React from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Box } from "@mui/material";
import ReviewLinkView from "src/sections/review-link/ReviewLinkView";

/**
 * Public outside-reviewer page `/review/:token` (W4-4): no app chrome and no
 * sign-in; the token is the reviewer's own revocable link.
 */
export default function ReviewLinkPage() {
  const { token } = useParams();
  const [params] = useSearchParams();
  return (
    <>
      <Helmet>
        <title>Review</title>
      </Helmet>
      <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
        <ReviewLinkView
          token={token}
          startItemId={params.get("item") ?? undefined}
        />
      </Box>
    </>
  );
}
