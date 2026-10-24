import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";

import Card from "@mui/material/Card";
import Stack from "@mui/material/Stack";
import LoadingButton from "@mui/lab/LoadingButton";
import InputAdornment from "@mui/material/InputAdornment";

import Iconify from "src/components/iconify";
import { useSnackbar } from "src/components/snackbar";
import FormProvider, { RHFTextField } from "src/components/hook-form";
import logger from "src/utils/logger";

// ----------------------------------------------------------------------

export default function AccountSocialLinks({ socialLinks }) {
  const { enqueueSnackbar } = useSnackbar();

  const defaultValues = {
    facebook: socialLinks.facebook,
    instagram: socialLinks.instagram,
    linkedin: socialLinks.linkedin,
    twitter: socialLinks.twitter,
  };

  const methods = useForm({
    defaultValues,
  });

  const {
    handleSubmit,
    formState: { isSubmitting },
  } = methods;

  const onSubmit = handleSubmit(async () => {
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      enqueueSnackbar("Update success!");
    } catch (error) {
      logger.error("Failed to update social links", error);
    }
  });

  return (
    <FormProvider methods={methods} onSubmit={onSubmit}>
      <Stack component={Card} spacing={3} sx={{ p: 3 }}>
        {Object.keys(socialLinks).map((link) => (
          <RHFTextField
            key={link}
            name={link}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    width={24}
                    icon={
                      (link === "facebook" && "eva:facebook-fill") ||
                      (link === "instagram" && "ant-design:instagram-filled") ||
                      (link === "linkedin" && "eva:linkedin-fill") ||
                      (link === "twitter" && "eva:twitter-fill") ||
                      ""
                    }
                    color={
                      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                      (link === "facebook" && "#1877F2") ||
                      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                      (link === "instagram" && "#DF3E30") ||
                      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                      (link === "linkedin" && "#006097") ||
                      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
                      (link === "twitter" && "#1C9CEA") ||
                      ""
                    }
                  />
                </InputAdornment>
              ),
            }}
          />
        ))}

        <LoadingButton
          type="submit"
          variant="contained"
          loading={isSubmitting}
          sx={{ ml: "auto" }}
        >
          Save Changes
        </LoadingButton>
      </Stack>
    </FormProvider>
  );
}

AccountSocialLinks.propTypes = {
  socialLinks: PropTypes.object,
};
