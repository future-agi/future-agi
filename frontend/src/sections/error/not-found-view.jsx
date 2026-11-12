import React from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { RouterLink } from "src/routes/components";
import Iconify from "src/components/iconify";

const NotFoundView = () => (
  <Box
    display="flex"
    justifyContent="center"
    alignItems="center"
    height="100vh"
    width="100vw"
    flexDirection="column"
    overflow="hidden"
    bgcolor="background.default"
    position="absolute"
    left={0}
    top={0}
  >
    <Box
      display="flex"
      justifyContent="center"
      alignItems="center"
      flexDirection="column"
      maxWidth="480px"
      width="90%"
      gap={2}
      textAlign="center"
    >
      <Typography
        color="text.secondary"
        fontSize="14px"
        fontWeight={600}
        fontFamily="IBM Plex Sans"
        letterSpacing="0.08em"
      >
        404
      </Typography>

      <Typography
        component="h1"
        color="text.primary"
        fontSize={{ xs: "20px", sm: "24px" }}
        fontWeight={600}
        lineHeight={1.3}
        fontFamily="IBM Plex Sans"
      >
        Page not found
      </Typography>

      <Typography
        color="text.secondary"
        fontSize="13px"
        lineHeight={1.6}
        fontFamily="IBM Plex Sans"
      >
        This page does not exist, or your role does not have access to it.
      </Typography>

      <Button
        component={RouterLink}
        href="/"
        size="small"
        variant="contained"
        color="primary"
        sx={{
          mt: 1,
        }}
      >
        <Iconify
          icon="solar:alt-arrow-left-linear"
          width="15px"
          height="15px"
        />
        <Typography
          fontSize="12px"
          fontFamily="IBM Plex Sans"
          fontWeight={600}
          ml="5px"
        >
          Back to home
        </Typography>
      </Button>
    </Box>
  </Box>
);

export default NotFoundView;
