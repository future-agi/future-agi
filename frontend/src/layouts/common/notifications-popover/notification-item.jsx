import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import Avatar from "@mui/material/Avatar";
import Typography from "@mui/material/Typography";
import ListItemText from "@mui/material/ListItemText";
import ListItemAvatar from "@mui/material/ListItemAvatar";
import ListItemButton from "@mui/material/ListItemButton";

import { fToNow } from "src/utils/format-time";

import Label from "src/components/label";
import FileThumbnail from "src/components/file-thumbnail";

import { sanitizeNotificationHtml } from "./notification-sanitize";

// ----------------------------------------------------------------------

export default function NotificationItem({ notification }) {
  const renderAvatar = (
    <ListItemAvatar>
      {notification.avatar_url ? (
        <Avatar
          src={notification.avatar_url}
          sx={{ bgcolor: "background.neutral" }}
        />
      ) : (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{
            width: 40,
            height: 40,
            borderRadius: "50%",
            bgcolor: "background.neutral",
          }}
        >
          <Box
            component="img"
            src={`/assets/icons/notification/${
              (notification.type === "order" && "ic_order") ||
              (notification.type === "chat" && "ic_chat") ||
              (notification.type === "mail" && "ic_mail") ||
              (notification.type === "delivery" && "ic_delivery")
            }.svg`}
            sx={{ width: 24, height: 24 }}
          />
        </Stack>
      )}
    </ListItemAvatar>
  );

  const renderText = (
    <ListItemText
      disableTypography
      primary={reader(notification.title)}
      secondary={
        <Stack
          direction="row"
          alignItems="center"
          sx={{ typography: "caption", color: "text.disabled" }}
          divider={
            <Box
              sx={{
                width: 2,
                height: 2,
                bgcolor: "currentColor",
                mx: 0.5,
                borderRadius: "50%",
              }}
            />
          }
        >
          {fToNow(notification.created_at)}
          {notification.category}
        </Stack>
      }
    />
  );

  const renderUnReadBadge = notification.is_un_read && (
    <Box
      sx={{
        top: 26,
        width: 8,
        height: 8,
        right: 20,
        borderRadius: "50%",
        bgcolor: "info.main",
        position: "absolute",
      }}
    />
  );

  const friendAction = (
    <Stack spacing={1} direction="row" sx={{ mt: 1.5 }}>
      <Button size="small" variant="contained">
        Accept
      </Button>
      <Button size="small" variant="outlined">
        Decline
      </Button>
    </Stack>
  );

  const projectAction = (
    <Stack alignItems="flex-start">
      <Box
        sx={{
          p: 1.5,
          my: 1.5,
          borderRadius: 1.5,
          color: "text.secondary",
          bgcolor: "background.neutral",
        }}
      >
        {reader(
          `<p><strong>@Jaydon Frankie</strong> feedback by asking questions or just leave a note of appreciation.</p>`,
        )}
      </Box>

      <Button size="small" variant="contained">
        Reply
      </Button>
    </Stack>
  );

  const fileAction = (
    <Stack
      spacing={1}
      direction="row"
      sx={{
        pl: 1,
        p: 1.5,
        mt: 1.5,
        borderRadius: 1.5,
        bgcolor: "background.neutral",
      }}
    >
      <FileThumbnail
        file="http://localhost:8080/httpsdesign-suriname-2015.mp3"
        sx={{ width: 40, height: 40 }}
      />

      <Stack
        spacing={1}
        direction={{ xs: "column", sm: "row" }}
        flexGrow={1}
        sx={{ minWidth: 0 }}
      >
        <ListItemText
          disableTypography
          primary={
            <Typography
              variant="subtitle2"
              component="div"
              sx={{ color: "text.secondary" }}
              noWrap
            >
              design-suriname-2015.mp3
            </Typography>
          }
          secondary={
            <Stack
              direction="row"
              alignItems="center"
              sx={{ typography: "caption", color: "text.disabled" }}
              divider={
                <Box
                  sx={{
                    mx: 0.5,
                    width: 2,
                    height: 2,
                    borderRadius: "50%",
                    bgcolor: "currentColor",
                  }}
                />
              }
            >
              <span>2.3 GB</span>
              <span>30 min ago</span>
            </Stack>
          }
        />

        <Button size="small" variant="outlined">
          Download
        </Button>
      </Stack>
    </Stack>
  );

  const tagsAction = (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ mt: 1.5 }}>
      <Label variant="outlined" color="info">
        Design
      </Label>
      <Label variant="outlined" color="warning">
        Dashboard
      </Label>
      <Label variant="outlined">Design system</Label>
    </Stack>
  );

  const paymentAction = (
    <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
      <Button size="small" variant="contained">
        Pay
      </Button>
      <Button size="small" variant="outlined">
        Decline
      </Button>
    </Stack>
  );

  return (
    <ListItemButton
      disableRipple
      sx={{
        p: 2.5,
        alignItems: "flex-start",
        borderBottom: (theme) => `dashed 1px ${theme.palette.divider}`,
      }}
    >
      {renderUnReadBadge}

      {renderAvatar}

      <Stack sx={{ flexGrow: 1 }}>
        {renderText}
        {notification.type === "friend" && friendAction}
        {notification.type === "project" && projectAction}
        {notification.type === "file" && fileAction}
        {notification.type === "tags" && tagsAction}
        {notification.type === "payment" && paymentAction}
      </Stack>
    </ListItemButton>
  );
}

NotificationItem.propTypes = {
  notification: PropTypes.object,
};

// ----------------------------------------------------------------------

function reader(data) {
  return (
    <Box
      data-testid="notification-title"
      dangerouslySetInnerHTML={{ __html: sanitizeNotificationHtml(data) }}
      sx={{
        mb: 0.5,
        "& p": { typography: "body2", m: 0 },
        "& a": { color: "inherit", textDecoration: "none" },
        "& strong": { typography: "subtitle2" },
      }}
    />
  );
}
