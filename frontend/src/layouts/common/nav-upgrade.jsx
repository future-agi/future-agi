import React, { useRef, useState } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useAuthContext } from "src/auth/hooks";
import { useTheme } from "@mui/material";
import ProfilePopover from "../dashboard/ProfilePopover";
import { alpha } from "@mui/material/styles";

// ----------------------------------------------------------------------

export default function NavUpgrade() {
  const { user } = useAuthContext();
  const theme = useTheme();
  const [isOpen, setOpen] = useState(false);
  const dropDownRef = useRef();
  const userIcon = user?.name
    ?.split(" ")
    .map((item) => item?.[0]?.toUpperCase())
    .slice(0, 2)
    .join("");

  return (
    <>
      <Stack
        ref={dropDownRef}
        sx={{
          px: 2,
          py: 1.25,
          height: "auto",
          minHeight: 52,
          bgcolor: "background.paper",
          mt: 0.5,
        }}
        direction={"row"}
        alignItems={"center"}
        spacing={1.5}
      >
        <Box
          onClick={() => setOpen(true)}
          sx={{
            display: "flex",
            alignItems: "center",
            flex: 1,
            cursor: "pointer",
            p: 0.5,
            borderRadius: 1,
            overflow: "hidden",
            transition: theme.transitions.create(["background-color"], {
              duration: theme.transitions.duration.shortest,
            }),
            "&:hover": {
              bgcolor: alpha(theme.palette.text.disabled, 0.08),
            },
          }}
        >
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              bgcolor: "background.neutral",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              mr: 1.5,
            }}
          >
            <Typography
              sx={{
                color: "text.secondary",
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              {userIcon}
            </Typography>
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 500,
                color: "text.primary",
                fontSize: "12px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {user.name}
            </Typography>
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: "12px",
                display: "block",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {user.email}
            </Typography>
          </Box>
        </Box>
      </Stack>
      <ProfilePopover
        anchorEl={dropDownRef?.current}
        open={isOpen}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
