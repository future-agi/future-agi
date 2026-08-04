import React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useNavigate } from "react-router-dom";

import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import axios, { endpoints } from "src/utils/axios";
import { useMutation } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import { Box, Divider } from "@mui/material";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import logger from "src/utils/logger";
import RightSectionAuth from "./RightSectionAuth";

// ----------------------------------------------------------------------

const SSOSchema = z.object({
  email: z.string().email("Enter valid email address"),
});

export default function SSOLogin() {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const defaultValues = { email: "" };

  const { handleSubmit, control } = useForm({
    resolver: zodResolver(SSOSchema),
    defaultValues,
  });

  const { mutate: initiateSSO, isPending: isLoading } = useMutation({
    mutationFn: async (data) => {
      const response = await axios.get(endpoints.auth.ssoLogin(data.email));
      return response.data;
    },
    onSuccess: (data) => {
      if (data.new_org) {
        logger.debug("SSO Response:", data);
        navigate(paths.auth.jwt.setup_org);
      } else {
        enqueueSnackbar(data.message || "SSO login initiated successfully", {
          variant: "success",
        });
      }
      trackEvent(Events.ssoLoginClicked, {
        [PropertyName.mode]: "email",
      });
    },
  });

  const onSubmit = handleSubmit((data) => {
    initiateSSO(data);
  });

  const renderHead = (
    <Stack sx={{ mb: 4 }}>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        sx={{
          fontSize: "28px",
          color: "text.primary",
          fontFamily: "Inter",
          lineHeight: "36px",
        }}
      >
        Sign in
      </Typography>
      <Typography
        fontWeight={"fontWeightSemiBold"}
        sx={{
          fontSize: "28px",
          lineHeight: "36px",
          color: "text.secondary",
          maxWidth: "480px",
          fontFamily: "Inter",
        }}
      >
        Enter your domain registered business email
      </Typography>
    </Stack>
  );

  const renderForm = (
    <Box display={"flex"} flexDirection={"column"} gap={2.5} maxWidth={"440px"}>
      <FormTextFieldV2
        label="Business Email"
        control={control}
        fieldName="email"
        fullWidth
        placeholder="joedoe@futureagi.com"
        size="small"
      />

      <LoadingButton
        fullWidth
        type="submit"
        variant="contained"
        loading={isLoading}
        sx={{ borderRadius: 0.5 }}
        color="primary"
      >
        Sign in
      </LoadingButton>
      <Divider>
        <Typography variant="body2" sx={{ color: "text.disabled" }}>
          or
        </Typography>
      </Divider>

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="center"
        spacing={0.5}
      >
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          Don{"’"}t have an account?
        </Typography>
        <Link
          component={RouterLink}
          to={paths.auth.jwt.register}
          variant="subtitle2"
        >
          Sign up
        </Link>
      </Stack>
    </Box>
  );

  return (
    <Box
      sx={{
        width: "100%",
        height: "100vh",
        display: "flex",
        position: "relative",
      }}
    >
      {/* Back Button */}
      {/* <IconButton
        onClick={handleBack}
        sx={{
          position: "absolute",
          top: 16,
          left: 16,
          zIndex: 1,
          color: "text.primary",
          bgcolor: "common.white",
          boxShadow: "0 2px 4px rgba(0,0,0,0.1)",
          "&:hover": {
            bgcolor: "common.white",
          },
        }}
      >
        <Iconify icon="eva:arrow-ios-back-fill" />
      </IconButton> */}

      {/* Left Side - Form */}
      <Box
        sx={{
          width: "50%",
          height: "100vh",
          display: "flex",
          justifyContent: "center",
          paddingY: "100px",
          bgcolor: "background.paper",
          overflowY: "auto",
        }}
      >
        <Box sx={{ width: "640px", px: 10, height: "fit-content" }}>
          <form onSubmit={onSubmit}>
            {renderHead}
            {renderForm}
          </form>
        </Box>
      </Box>

      {/* Right Side - Image with Text Overlay */}
      <Box
        sx={{
          width: "50%",
          height: "100%",
          backgroundColor: "background.neutral",
        }}
      >
        <RightSectionAuth />
      </Box>
    </Box>
  );
}
