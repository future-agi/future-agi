import React, { useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Popover from "@mui/material/Popover";
import Tooltip from "@mui/material/Tooltip";

import { EMAIL_UNVERIFIED, useLike } from "../api/queries";
import { exactCount } from "../lib/format";

import CountDefinition from "./CountDefinition";
import EmailVerifyNotice from "./EmailVerifyNotice";
import HubIcon from "./HubIcon";

// Like, with its count (SPEC §6.1). Signed-in browser sessions only; likers
// are private. Without a session the button is disabled and says why.
export default function LikeButton({
  publisher,
  name,
  likes,
  likedByMe,
  signedIn,
}) {
  const like = useLike(publisher, name);
  const anchor = useRef(null);
  const liked = Boolean(likedByMe);
  const unverified = like.isError && like.error?.code === EMAIL_UNVERIFIED;
  const label = !signedIn ? "Sign in to like" : liked ? "Liked" : "Like";
  const button = (
    <Button
      size="small"
      variant={liked ? "soft" : "outlined"}
      color={liked ? "primary" : "inherit"}
      disabled={!signedIn || like.isPending}
      onClick={() => like.mutate(liked)}
      aria-pressed={liked}
      data-testid="hub-like-button"
      startIcon={<HubIcon name="heart" size={15} filled={liked} />}
      sx={{ minWidth: 0, borderColor: "divider" }}
    >
      {signedIn ? label : "Sign in to like"}
      <Box
        component="span"
        sx={{ ml: 1, color: "text.secondary", fontWeight: 500 }}
        data-testid="hub-like-count"
      >
        {exactCount(likes)}
      </Box>
    </Button>
  );
  return (
    <Box ref={anchor} sx={{ display: "inline-flex", alignItems: "center" }}>
      {unverified ? (
        <span>{button}</span>
      ) : like.isError ? (
        <Tooltip title={like.error?.message || "The like did not save"} open>
          <span>{button}</span>
        </Tooltip>
      ) : (
        <span>{button}</span>
      )}
      <CountDefinition kind="likes" />
      <Popover
        open={unverified}
        anchorEl={anchor.current}
        onClose={() => like.reset()}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: { sx: { width: 360, maxWidth: "calc(100vw - 32px)" } },
        }}
      >
        <EmailVerifyNotice action="like entries" />
      </Popover>
    </Box>
  );
}

LikeButton.propTypes = {
  publisher: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  likes: PropTypes.number,
  likedByMe: PropTypes.bool,
  signedIn: PropTypes.bool,
};
