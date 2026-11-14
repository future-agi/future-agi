import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import RadioField from "src/components/RadioField/RadioField";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import { AVAILABLE_ROLES, DEFAULT_ROLES, GOALS_LIST } from "./constants";
import { userDataSchema } from "./zodSchema";

export default function OnboardingProfileForm({
  initialData,
  activeStep,
  setActiveStep,
  saveUserData,
  isSavingUserData,
}) {
  const customRole = !DEFAULT_ROLES.includes(initialData.role);
  // The first successful read seeds this wizard's draft; refetches must not reset it.
  const userForm = useForm({
    resolver: zodResolver(userDataSchema),
    defaultValues: {
      role: customRole ? "" : initialData.role || "",
      customRole: customRole ? initialData.role || "" : "",
      goals: GOALS_LIST.map((goal) =>
        (initialData.goals ?? []).some(
          (g) => g.toLowerCase() === goal.label.toLowerCase(),
        ),
      ),
    },
  });
  const customRoleValue = userForm.watch("customRole");
  const roleValue = userForm.watch("role");

  useEffect(() => {
    if (customRoleValue) {
      userForm.setValue("role", "");
    }
  }, [customRoleValue, userForm]);

  useEffect(() => {
    if (roleValue) {
      userForm.setValue("customRole", "");
    }
  }, [roleValue, userForm]);

  const onSubmitUserData = (data, isSkipped = false) => {
    if (isSavingUserData) {
      return;
    }
    saveUserData({
      role: data.customRole || data.role,
      goals: isSkipped
        ? []
        : GOALS_LIST.filter((_, index) => data.goals[index]).map(
            (goal) => goal.label,
          ),
    });
  };

  switch (activeStep) {
    case 0:
      return (
        <Stack spacing={2} width="440px">
          <Box>
            <Typography
              fontWeight={"fontWeightSemiBold"}
              sx={{
                fontSize: "24px",
                color: "text.primary",
                fontFamily: "Inter",
                lineHeight: "36px",
              }}
            >
              What&apos;s your role
            </Typography>
            <Typography
              fontWeight={"fontWeightSemiBold"}
              sx={{
                fontSize: "24px",
                color: "text.secondary",
                fontFamily: "Inter",
                lineHeight: "36px",
              }}
            >
              Select the job title you most identify with
            </Typography>
          </Box>

          <RadioField
            custom
            control={userForm.control}
            fieldName="role"
            optionColor="text.primary"
            labelColor="text.primary"
            label={undefined}
            groupSx={{ padding: 0, marginLeft: -1 }}
            options={AVAILABLE_ROLES}
          />

          <Typography variant="M3" fontWeight={"fontWeightMedium"}>
            Don&apos;t see your role?
          </Typography>

          <Controller
            name="customRole"
            control={userForm.control}
            render={({ field, fieldState: { error } }) => (
              <TextField
                {...field}
                fullWidth
                size="small"
                placeholder="Tell us about your role"
                variant="outlined"
                error={!!error}
                helperText={error?.message}
                sx={{
                  backgroundColor: "background.neutral",
                  borderRadius: "4px",
                }}
              />
            )}
          />

          <Button
            variant="contained"
            disabled={!roleValue && !customRoleValue}
            onClick={() => setActiveStep(1)}
            color="primary"
          >
            Continue
          </Button>
        </Stack>
      );

    case 1:
      return (
        <Stack spacing={2} width="440px">
          <Box>
            <Typography
              fontWeight={"fontWeightSemiBold"}
              sx={{
                fontSize: "24px",
                color: "text.primary",
                fontFamily: "Inter",
                lineHeight: "36px",
              }}
            >
              Let&apos;s get started
            </Typography>
            <Typography
              fontWeight={"fontWeightSemiBold"}
              sx={{
                fontSize: "24px",
                color: "text.secondary",
                fontFamily: "Inter",
                lineHeight: "36px",
              }}
            >
              Tell us about your goals
            </Typography>
          </Box>

          <Stack spacing={1}>
            {GOALS_LIST.map((goal, index) => (
              <FormCheckboxField
                key={goal.id}
                isCustom={true}
                labelPlacement="end"
                control={userForm.control}
                fieldName={`goals.${index}`}
                label={goal.label}
                helperText={goal.description}
              />
            ))}
          </Stack>
          <LoadingButton
            size="large"
            variant="contained"
            loading={isSavingUserData}
            disabled={isSavingUserData || (!roleValue && !customRoleValue)}
            onClick={userForm.handleSubmit((data) =>
              onSubmitUserData(data, false),
            )}
            color="primary"
          >
            Continue
          </LoadingButton>

          <Typography
            textAlign="center"
            onClick={
              !isSavingUserData
                ? userForm.handleSubmit((data) => onSubmitUserData(data, true))
                : undefined
            }
            sx={{
              cursor: isSavingUserData ? "not-allowed" : "pointer",
              opacity: isSavingUserData ? 0.6 : 1,
            }}
            color="primary.main"
            variant="s1.2"
            fontWeight={"fontWeightMedium"}
          >
            Skip for now
          </Typography>
        </Stack>
      );

    default:
      return null;
  }
}

OnboardingProfileForm.propTypes = {
  initialData: PropTypes.shape({
    role: PropTypes.string,
    goals: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  activeStep: PropTypes.number.isRequired,
  setActiveStep: PropTypes.func.isRequired,
  saveUserData: PropTypes.func.isRequired,
  isSavingUserData: PropTypes.bool.isRequired,
};
