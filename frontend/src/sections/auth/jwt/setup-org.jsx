import React, {
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react";
import { Helmet } from "react-helmet-async";
import {
  Box,
  Stack,
  Typography,
  TextField,
  IconButton,
  Chip,
  styled,
  MobileStepper,
  Button,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { Controller, useForm, useFieldArray, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useAuthContext } from "src/auth/hooks";
import { DEFAULT_ROLES, GOALS_LIST, ROLE_OPTIONS } from "./constants";
import { organizationSchema, userDataSchema } from "./zodSchema";
import { generateNameFromEmail } from "./common";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { useSearchParams } from "react-router-dom";
import {
  persistSetupCompletionReturnTo,
  resolveSetupCompletionHref,
  shouldShowInviteStepAfterProfileSave,
} from "./setup-org-routing";
import {
  trackSetupOrgInvitesSaved,
  trackSetupOrgProfileSaved,
  trackSetupOrgQuickStartClicked,
  trackSetupOrgQuickStartProfileSaveFailed,
  trackSetupOrgQuickStartsViewed,
} from "./setup-org-analytics";
import {
  isSetupOrgFirstSetupQuickStart,
  persistSetupQuickStartAttribution,
  SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS,
  SETUP_ORG_SAMPLE_PREVIEW_QUICK_START_ID,
} from "./setup-org-quick-starts";

const QUICK_START_ROLE = "AI Builder";

const SETUP_SIDE_PANEL_STEPS = [
  {
    icon: "mdi:cursor-default-click-outline",
    label: "Choose one product path",
    description: "Pick the workflow you want to make real first.",
  },
  {
    icon: "mdi:clipboard-check-outline",
    label: "Start with one action",
    description: "The workspace opens with the first action selected.",
  },
  {
    icon: "mdi:database-eye-outline",
    label: "Complete real setup",
    description: "Sample data stays available, but it does not finish setup.",
  },
];

const normalizeGoalValue = (value) =>
  String(value || "")
    .trim()
    .toLowerCase();

const goalMatchesSavedValue = (goal, savedGoal) => {
  const saved = normalizeGoalValue(savedGoal);
  if (!saved) return false;
  return [goal.id, goal.label, ...(goal.aliases || [])].some(
    (candidate) => normalizeGoalValue(candidate) === saved,
  );
};

const setupSaveFailureReason = (error) =>
  error?.response?.data?.code ||
  error?.response?.data?.result?.error_code ||
  error?.code ||
  "unknown_error";

const DotsStepper = styled(MobileStepper)(({ theme }) => ({
  background: "transparent",
  justifyContent: "flex-start",
  padding: 0,
  "& .MuiMobileStepper-dot": {
    width: 12,
    height: 12,
    margin: "0 6px",
    backgroundColor: theme.palette.action.disabled,
    transition: "all 0.3s ease",
  },
  "& .MuiMobileStepper-dotActive": {
    width: 40,
    borderRadius: 8,
    backgroundColor: theme.palette.text.primary,
  },
}));

const MemberRow = React.memo(
  ({ member, index, editable = false, getStarted, control, onRemove }) => {
    const roleOptions = useMemo(() => {
      const isOwner = member.organization_role === "Owner";
      return isOwner
        ? [...ROLE_OPTIONS, { label: "Owner", value: "Owner" }]
        : ROLE_OPTIONS;
    }, [member.organization_role]);

    if (!editable) {
      return (
        <Stack
          direction={{ xs: "column", sm: "row" }}
          alignItems="center"
          sx={{
            position: "relative",
            gap: { xs: 1, sm: 2 },
            width: "100%",
            pr: { xs: 0, sm: 5 },
            pt: 0,
            mt: 2,
          }}
        >
          <TextField
            placeholder="Email"
            size="small"
            label="Email"
            value={member.email}
            disabled
            sx={{ flex: 1.8 }}
          />

          <FormSearchSelectFieldState
            size="small"
            label="Role"
            value={member.organization_role}
            disabled
            showClear={false}
            sx={{ flex: 1.5, minWidth: 110, borderRadius: 1 }}
            options={roleOptions}
          />
        </Stack>
      );
    }
    return (
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems="flex-start"
        sx={{
          position: "relative",
          gap: { xs: 1, sm: 2 },
          width: "100%",
          pr: { xs: 0, sm: 5 },
          pt: 0,
          mt: 2,
        }}
      >
        <FormTextFieldV2
          control={control}
          fieldName={`members.${index}.email`}
          label="Email"
          placeholder="Email"
          fieldType="text"
          size="small"
          sx={{ flex: 1.8 }}
          autoFocus={false}
        />

        <Controller
          name={`members.${index}.organization_role`}
          control={control}
          render={({ field }) => (
            <FormSearchSelectFieldState
              {...field}
              size="small"
              label="Role"
              showClear={false}
              sx={{ flex: 1.5, minWidth: 110, borderRadius: 0.5 }}
              options={roleOptions}
            />
          )}
        />

        {member.organization_role !== "Owner" && (
          <IconButton
            className="remove-btn"
            onClick={() => onRemove(index)}
            sx={{
              position: "absolute",
              right: getStarted ? 0 : -10,
              color: "text.primary",
              cursor: "pointer",
              "&:hover": {
                backgroundColor: "transparent",
              },
            }}
          >
            <SvgColor
              height={20}
              width={20}
              src="/assets/icons/ic_delete.svg"
            />
          </IconButton>
        )}
      </Stack>
    );
  },
);

MemberRow.displayName = "MemberRow";
MemberRow.propTypes = {
  member: PropTypes.object.isRequired,
  index: PropTypes.number,
  editable: PropTypes.bool,
  getStarted: PropTypes.bool,
  control: PropTypes.object,
  onRemove: PropTypes.func,
};

const SetupOrgSidePanel = () => (
  <Box
    sx={{
      width: "100%",
      height: "100%",
      minHeight: "100dvh",
      p: 4,
      bgcolor: "background.neutral",
      display: "flex",
      flexDirection: "column",
    }}
  >
    <Stack direction="row" gap={0.75} alignItems="center">
      <Box
        component="img"
        sx={{ height: 44, width: 44 }}
        src="/favicon/logo.svg"
        alt="FutureAGI"
      />

      <SvgColor
        src="/logo/future_agi_text.svg"
        sx={{ height: 20, width: 128, color: "text.primary" }}
      />
    </Stack>

    <Stack
      spacing={3}
      sx={{
        maxWidth: 520,
        width: "100%",
        mx: "auto",
        my: "auto",
      }}
    >
      <Stack spacing={1}>
        <Typography variant="overline" color="text.secondary">
          First setup
        </Typography>
        <Typography variant="h4">Choose what to set up first</Typography>
        <Typography variant="body1" color="text.secondary">
          Pick the path that matches your first job. The workspace will open
          with that action selected.
        </Typography>
      </Stack>

      <Stack spacing={1.25}>
        {SETUP_SIDE_PANEL_STEPS.map((step) => (
          <Stack
            key={step.label}
            direction="row"
            spacing={1.25}
            alignItems="flex-start"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
              p: 1.5,
            }}
          >
            <Box
              sx={{
                width: 34,
                height: 34,
                borderRadius: 1,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                bgcolor: "primary.main",
                color: "primary.contrastText",
                flexShrink: 0,
              }}
            >
              <Iconify icon={step.icon} width={18} />
            </Box>
            <Stack spacing={0.25}>
              <Typography variant="subtitle2">{step.label}</Typography>
              <Typography variant="body2" color="text.secondary">
                {step.description}
              </Typography>
            </Stack>
          </Stack>
        ))}
      </Stack>
    </Stack>
  </Box>
);

const useOrganizationInitialData = (isOwner, user) => {
  const { enqueueSnackbar } = useSnackbar();
  const [isLoading, setIsLoading] = useState(true);
  const [initialData, setInitialData] = useState(null);

  useEffect(() => {
    const fetchOrgDetails = async () => {
      setIsLoading(true);
      try {
        const response = await axios.get(endpoints.auth.create_org);
        const orgDetails = response?.data?.result;

        const prefilledMembers =
          orgDetails.results.length > 0
            ? orgDetails?.results?.reverse().map((member) => ({
                email: member.email,
                name: member.name,
                organization_role: member.organization_role,
                disabled: true,
              }))
            : [
                {
                  email: "",
                  name: "",
                  organization_role: "Owner",
                  disabled: false,
                },
              ];

        const organizationName =
          orgDetails.org_name ||
          user?.organization?.display_name ||
          user?.organization?.name ||
          generateNameFromEmail(user?.email) ||
          "";

        setInitialData({
          orgName: organizationName,
          members: prefilledMembers,
        });
      } catch (error) {
        enqueueSnackbar("Failed to fetch organization details", {
          variant: "error",
        });
        setInitialData({
          orgName:
            user?.organization?.display_name ||
            user?.organization?.name ||
            generateNameFromEmail(user?.email) ||
            "",
          members: [
            {
              email: "",
              name: "",
              organization_role: "Owner",
              disabled: false,
            },
          ],
        });
      } finally {
        setIsLoading(false);
      }
    };
    if (!isOwner) {
      setIsLoading(false);
      return;
    }

    fetchOrgDetails();
  }, [
    enqueueSnackbar,
    isOwner,
    user?.email,
    user?.organization?.display_name,
    user?.organization?.name,
  ]);

  return { initialData, isLoading };
};

const SetupOrganization = ({ getStarted = false }) => {
  const queryClient = useQueryClient();
  const quickStartOptionRef = useRef(null);
  const quickStartsViewedRef = useRef(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const rawActiveStep = parseInt(searchParams.get("step") || "0", 10);
  const activeStep = rawActiveStep === 2 ? 2 : 0;
  const finishSetup = useCallback((quickStartOption) => {
    const completionHref = resolveSetupCompletionHref(quickStartOption);
    localStorage.setItem("initial-render", "done");
    localStorage.removeItem("redirectUrl");
    persistSetupCompletionReturnTo(completionHref);
    window.location.href = completionHref;
  }, []);

  const setActiveStep = useCallback(
    (newStep) => {
      const params = new URLSearchParams(searchParams);
      params.set("step", newStep.toString());
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const { enqueueSnackbar } = useSnackbar();
  const { updateUserData, user } = useAuthContext();
  const isOwner = user?.organization_role === "Owner";
  const { initialData, isLoading: isFetchingInitialData } =
    useOrganizationInitialData(isOwner, user);

  const { data: invitesData, refetch: refetchInvites } = useQuery({
    queryKey: ["owner-org-invites"],
    queryFn: async () => {
      const response = await axios.get(endpoints.settings.teams.getMemberList, {
        params: { is_active: false },
      });
      return response;
    },
    enabled: isOwner,
    select: (d) => d?.data?.result,
  });

  const { data: userOnboardingData } = useQuery({
    queryKey: ["user-role-goals"],
    queryFn: async () => {
      const response = await axios.get(endpoints.auth.user_onboarding_info);
      return response;
    },

    select: (d) => d?.data?.result,
  });

  const defaultValuesForUserForm = useCallback(() => {
    const customRole = !DEFAULT_ROLES?.includes(userOnboardingData?.role);
    const goalsArray = GOALS_LIST.map((goal) => {
      const isSelected = userOnboardingData?.goals?.some((g) =>
        goalMatchesSavedValue(goal, g),
      );

      return isSelected;
    });

    return {
      role: customRole ? "" : userOnboardingData?.role || "",
      customRole: customRole ? userOnboardingData?.role || "" : "",
      goals: goalsArray,
    };
  }, [userOnboardingData]);

  const userForm = useForm({
    resolver: zodResolver(userDataSchema),
    defaultValues: defaultValuesForUserForm(),
  });
  useEffect(() => {
    if (userOnboardingData) {
      const newDefaults = defaultValuesForUserForm();

      userForm.reset(newDefaults);
    }
  }, [userOnboardingData]);

  const { mutate: saveUserData, isPending: isSavingUserData } = useMutation({
    mutationFn: async (data) => {
      return await axios.post(endpoints.auth.user_onboarding_info, data);
    },
    meta: {
      errorHandled: true,
    },
    onSuccess: (data, variables) => {
      const quickStartOption = quickStartOptionRef.current;
      const shouldFinishQuickStart = Boolean(quickStartOption);
      quickStartOptionRef.current = null;
      enqueueSnackbar("Profile updated successfully", { variant: "success" });
      const provider = localStorage.getItem("signupProvider");
      trackSetupOrgProfileSaved({
        goals: variables?.goals,
        provider,
        quickStartGoal: quickStartOption?.goal,
        quickStartId: quickStartOption?.id,
        quickStartPrimaryPath: quickStartOption?.primaryPath,
        quickStartRequested: shouldFinishQuickStart,
        role: variables?.role,
      });
      localStorage.removeItem("signupProvider");
      if (
        shouldShowInviteStepAfterProfileSave({
          isOwner,
          quickStartRequested: shouldFinishQuickStart,
        })
      ) {
        setActiveStep(2);
      } else {
        updateUserData({
          role: variables?.role,
          goals: variables?.goals || [],
          onboarding_completed: Boolean(
            variables?.role && variables?.goals?.length,
          ),
        });
        finishSetup(quickStartOption);
      }
    },
    onError: (error) => {
      const quickStartOption = quickStartOptionRef.current;
      const shouldFinishQuickStart = Boolean(quickStartOption);
      quickStartOptionRef.current = null;
      if (shouldFinishQuickStart) {
        trackSetupOrgQuickStartProfileSaveFailed({
          quickStartGoal: quickStartOption.goal,
          quickStartId: quickStartOption.id,
          quickStartPrimaryPath: quickStartOption.primaryPath,
          reason: setupSaveFailureReason(error),
          status: error?.response?.status || error?.status,
        });
        enqueueSnackbar("Could not save your setup choice. Please try again.", {
          variant: "error",
        });
        return;
      }
      enqueueSnackbar(error?.message || "Failed to save profile", {
        variant: "error",
      });
    },
  });
  const customRoleValue = userForm.watch("customRole");
  const roleValue = userForm.watch("role");
  useEffect(() => {
    if (activeStep !== 0 || quickStartsViewedRef.current) {
      return;
    }

    quickStartsViewedRef.current = true;
    trackSetupOrgQuickStartsViewed({
      quickStarts: SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS,
    });
  }, [activeStep]);

  const handleProductLoopQuickStart = useCallback(
    (option) => {
      if (isSavingUserData || quickStartOptionRef.current) {
        return;
      }

      quickStartOptionRef.current = option;
      persistSetupQuickStartAttribution({
        quickStartGoal: option.goal,
        quickStartId: option.id,
        quickStartPrimaryPath: option.primaryPath,
      });
      trackSetupOrgQuickStartClicked({
        quickStartGoal: option.goal,
        quickStartId: option.id,
        quickStartPrimaryPath: option.primaryPath,
      });
      saveUserData({
        role: customRoleValue || roleValue || QUICK_START_ROLE,
        goals: [option.goal],
      });
    },
    [customRoleValue, isSavingUserData, roleValue, saveUserData],
  );
  const handleProductLoopQuickStartPointerUp = useCallback(
    (event, option) => {
      if (event.pointerType === "mouse" && event.button !== 0) {
        return;
      }
      handleProductLoopQuickStart(option);
    },
    [handleProductLoopQuickStart],
  );
  const handleSamplePreviewQuickStart = useCallback(
    (option) => {
      if (isSavingUserData || quickStartOptionRef.current) {
        return;
      }

      persistSetupQuickStartAttribution({
        quickStartGoal: option.goal,
        quickStartId: option.id,
        quickStartPrimaryPath: option.primaryPath,
      });
      trackSetupOrgQuickStartClicked({
        quickStartGoal: option.goal,
        quickStartId: option.id,
        quickStartPrimaryPath: option.primaryPath,
      });
      finishSetup(option);
    },
    [finishSetup, isSavingUserData],
  );

  const renderProductLoopQuickStart = (option) => {
    const ButtonComponent = option.featured ? LoadingButton : Button;
    return (
      <ButtonComponent
        key={option.id}
        fullWidth
        sx={{
          borderRadius: 0.5,
          minHeight: { xs: 188, sm: 128 },
          height: "auto",
          alignItems: "flex-start",
          justifyContent: "flex-start",
          px: 1.75,
          py: 1.25,
          textAlign: "left",
          whiteSpace: "normal",
          "& .MuiButton-startIcon": {
            mt: 0.2,
          },
          "& .MuiButton-endIcon": {
            alignSelf: "flex-start",
          },
        }}
        variant={option.featured ? "contained" : "outlined"}
        loading={option.featured ? isSavingUserData : undefined}
        disabled={isSavingUserData}
        aria-label={option.buttonLabel}
        onClick={() => handleProductLoopQuickStart(option)}
        onPointerUp={(event) =>
          handleProductLoopQuickStartPointerUp(event, option)
        }
        color="primary"
        startIcon={
          <Iconify
            icon={option.icon}
            width={18}
            sx={{ flexShrink: 0, mt: 0.25 }}
          />
        }
      >
        <Stack
          component="span"
          spacing={1}
          sx={{ display: "flex", minWidth: 0, width: "100%" }}
        >
          <Stack
            component="span"
            direction={{ xs: "column", sm: "row" }}
            spacing={0.75}
            justifyContent="space-between"
            alignItems={{ xs: "flex-start", sm: "center" }}
            sx={{ width: "100%" }}
          >
            <Stack
              component="span"
              spacing={0.25}
              sx={{ display: "flex", minWidth: 0 }}
            >
              <Typography
                component="span"
                variant="subtitle2"
                sx={{ lineHeight: 1.2 }}
              >
                {option.buttonLabel}
              </Typography>
              <Typography
                component="span"
                variant="caption"
                sx={{
                  color: option.featured
                    ? "primary.contrastText"
                    : "text.secondary",
                  lineHeight: 1.25,
                }}
              >
                {option.shortDescription}
              </Typography>
            </Stack>
            {option.featured ? (
              <Chip
                size="small"
                label="Recommended"
                sx={{
                  color: "primary.contrastText",
                  borderColor: "rgba(255,255,255,0.48)",
                }}
                variant="outlined"
              />
            ) : null}
          </Stack>
          <Box
            component="span"
            sx={{
              display: "block",
              border: "1px solid",
              borderColor: option.featured
                ? "rgba(255,255,255,0.36)"
                : "divider",
              borderRadius: 0.5,
              px: 1,
              py: 0.75,
              bgcolor: option.featured
                ? "rgba(255,255,255,0.10)"
                : "background.neutral",
              width: "100%",
            }}
          >
            <Stack component="span" spacing={0.25} sx={{ display: "flex" }}>
              <Typography
                component="span"
                variant="caption"
                sx={{
                  color: option.featured
                    ? "primary.contrastText"
                    : "text.secondary",
                  opacity: option.featured ? 0.82 : 1,
                  textTransform: "uppercase",
                }}
              >
                First action
              </Typography>
              <Typography
                component="span"
                variant="body2"
                sx={{
                  color: option.featured
                    ? "primary.contrastText"
                    : "text.primary",
                  fontWeight: "fontWeightMedium",
                }}
              >
                {option.firstActionLabel}
              </Typography>
              <Typography
                component="span"
                variant="caption"
                sx={{
                  color: option.featured
                    ? "primary.contrastText"
                    : "text.secondary",
                  opacity: option.featured ? 0.88 : 1,
                  lineHeight: 1.25,
                }}
              >
                Then: {option.pathPreview}
              </Typography>
            </Stack>
          </Box>
        </Stack>
      </ButtonComponent>
    );
  };

  const renderSampleQuickStart = (option) => (
    <Box
      data-testid="setup-org-sample-quick-start"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack spacing={1.25}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify icon={option.icon} width={20} />
          <Typography variant="subtitle2">Preview sample data</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary">
          Optional preview only. This does not complete setup; choose a product
          path above to set up your workspace with real data.
        </Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            px: 1,
            py: 0.75,
            bgcolor: "background.neutral",
          }}
        >
          <Typography variant="caption" color="text.secondary">
            Preview: {option.firstActionLabel}. {option.pathPreview}
          </Typography>
        </Box>
        <Button
          fullWidth
          sx={{ borderRadius: 0.5, minHeight: 48 }}
          variant="text"
          disabled={isSavingUserData}
          aria-label={option.previewButtonLabel || option.buttonLabel}
          onClick={() => handleSamplePreviewQuickStart(option)}
          color="primary"
          startIcon={<Iconify icon="mdi:chart-timeline-variant" width={18} />}
        >
          {option.previewButtonLabel || option.buttonLabel}
        </Button>
      </Stack>
    </Box>
  );

  const renderProductLoopQuickStarts = () => {
    const sampleQuickStart = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
      (option) => option.id === SETUP_ORG_SAMPLE_PREVIEW_QUICK_START_ID,
    );
    const productQuickStarts = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.filter(
      (option) => isSetupOrgFirstSetupQuickStart(option),
    );

    return (
      <Stack spacing={1.5}>
        <Stack spacing={0.5}>
          <Typography variant="body2" color="text.secondary">
            Choose one path. The next screen opens with the first action
            selected and the remaining setup steps visible.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Team invites can wait until setup is working.
          </Typography>
        </Stack>

        <Box
          data-testid="setup-org-product-quick-starts"
          sx={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 1,
          }}
        >
          {productQuickStarts.map(renderProductLoopQuickStart)}
        </Box>

        {sampleQuickStart ? renderSampleQuickStart(sampleQuickStart) : null}
      </Stack>
    );
  };

  const orgForm = useForm({
    resolver: zodResolver(organizationSchema),
    mode: "onChange",
    defaultValues: {
      orgName: "",
      members: [
        {
          email: "",
          name: "",
          organization_role: "Owner",
          disabled: false,
        },
      ],
    },
  });

  const {
    setError,
    formState: { isValid: IsOrgFormValid },
  } = orgForm;

  const { fields, append, remove } = useFieldArray({
    control: orgForm.control,
    name: "members",
  });
  const memberToadd = useWatch({
    control: orgForm.control,
    name: "members",
  });
  useEffect(() => {
    if (initialData) {
      orgForm.reset(initialData);
    }
  }, [initialData, orgForm]);

  useEffect(() => {
    if (customRoleValue) {
      userForm.setValue("role", "");
    }
  }, [customRoleValue]);

  useEffect(() => {
    if (roleValue) {
      userForm.setValue("customRole", "");
    }
  }, [roleValue]);

  const addMember = useCallback(() => {
    append({
      email: "",
      name: "",
      organization_role: "Member",
      disabled: false,
    });
  }, [append]);

  const removeMember = useCallback(
    (index) => {
      remove(index);
    },
    [remove],
  );

  const { prefilledMembers, newMembers } = useMemo(() => {
    const prefilled = fields.filter((field) => field.disabled);
    const newOnes = fields.filter((field) => !field.disabled);
    return { prefilledMembers: prefilled, newMembers: newOnes };
  }, [fields]);

  const { mutate: createOrg, isPending: isCreating } = useMutation({
    mutationFn: async (data) => {
      const membersToSend = data.members
        .map((m, i) => ({ ...m, originalIndex: i }))
        .filter((m) => !m.disabled && (m.email || "").trim())
        .map((m) => ({
          index: m.originalIndex,
          email: m.email,
          name: m.name || generateNameFromEmail(m.email || ""),
          organization_role: m.organization_role,
        }));

      const payload = {
        org_name: data.orgName,
        members: membersToSend,
      };

      const response = await axios.post(
        endpoints.settings.teams.inviteMember,
        payload,
      );

      if (response.status === 200 || response.status === 201) {
        const message =
          response.data?.result?.created_members?.length === 0
            ? "Organization Setup Successfully."
            : "Invite sent successfully.";
        enqueueSnackbar(message, { variant: "success" });
      } else {
        const errors = response.data?.result?.errors;
        if (Array.isArray(errors) && errors.length > 0) {
          errors.forEach((error, index) => {
            enqueueSnackbar(error.error, {
              variant: "error",
              key: `error-${index}`,
            });
          });
        } else {
          enqueueSnackbar(
            response.data?.result?.errors || "Something went wrong.",
            { variant: "error" },
          );
        }
      }
      return response;
    },
    meta: { errorHandled: true },

    onSuccess: (_, variables) => {
      refetchInvites();
      trackSetupOrgInvitesSaved({ members: variables?.members });

      orgForm.reset();

      if (!getStarted) {
        updateUserData({ onboarding_completed: true });
        finishSetup();
      }
    },

    onError: (error) => {
      const apiErrors = error?.result?.errors || [];
      const members = orgForm.getValues("members");
      if (!apiErrors.length) {
        enqueueSnackbar(error?.result || "Something went wrong.", {
          variant: "error",
        });
        orgForm.reset();
        return;
      }

      const hasMemberErrors = apiErrors.some(
        (err) => err?.email || typeof err?.index === "number",
      );

      if (hasMemberErrors) {
        queryClient.invalidateQueries("owner-org-invites");

        const errorEmails = apiErrors
          .map((err) => err.email?.trim().toLowerCase())
          .filter(Boolean);
        const remainingMembers = members.filter((member) => {
          const isDisabled = member.disabled;
          const hasError = errorEmails.includes(
            member.email.trim().toLowerCase(),
          );
          return isDisabled || hasError;
        });

        orgForm.setValue("members", remainingMembers);

        setTimeout(() => {
          apiErrors.forEach((err) => {
            const memberIndex = remainingMembers.findIndex(
              (m) =>
                m.email.trim().toLowerCase() ===
                (err.email || "").trim().toLowerCase(),
            );

            if (memberIndex !== -1) {
              setError(`members.${memberIndex}.email`, {
                type: "manual",
                message: err.error || "Invalid member email",
              });
            }
          });
        }, 0);

        enqueueSnackbar("Some users already exist in another organisation", {
          variant: "error",
        });
      } else {
        apiErrors.forEach((err, index) => {
          enqueueSnackbar(err.error || "Something went wrong", {
            variant: "error",
            key: `error-${index}`,
          });
        });
      }
    },
  });

  const handleOrgSubmit = orgForm.handleSubmit((data) => {
    const processedData = {
      ...data,
      members: data?.members.map((member) => ({
        ...member,
        name: member.name || generateNameFromEmail(member.email || ""),
      })),
    };
    createOrg(processedData);
  });
  const renderOrgSetup = () => (
    <Box>
      <form onSubmit={handleOrgSubmit} style={{ width: "100%" }}>
        <Stack spacing={2} sx={{ mx: "auto", pb: getStarted ? "16px" : "0" }}>
          {!getStarted && (
            <FormTextFieldV2
              control={orgForm.control}
              fieldName="orgName"
              label="Organization Name"
              placeholder="Add organization name"
              fieldType="text"
              size="small"
              fullWidth
              sx={{
                "& .MuiOutlinedInput-root": {
                  borderRadius: 0.5,
                  bgcolor: "background.paper",
                },
              }}
            />
          )}

          <Typography variant="m2" fontWeight="fontWeightMedium">
            This is optional. You can add teammates later.
          </Typography>

          <Box
            sx={{
              width: "100%",
              height: getStarted ? "250px" : "220px",
              bgcolor: "background.paper",
              overflowY: "auto",
              scrollbarWidth: "none",
              scrollBehavior: "auto",
              "&::-webkit-scrollbar": {
                display: "none",
              },
              msOverflowStyle: "none",
            }}
          >
            {/* Prefilled members */}
            {prefilledMembers.length > 0 && (
              <Box mt={2}>
                {prefilledMembers.map((member) => {
                  const originalIndex = fields.findIndex(
                    (f) => f.id === member.id,
                  );
                  return (
                    <MemberRow
                      key={member.id}
                      member={member}
                      index={originalIndex}
                      getStarted={getStarted}
                      editable={false}
                      control={orgForm.control}
                    />
                  );
                })}
              </Box>
            )}

            {/* Invited members from API */}
            {invitesData?.results?.length > 0 && (
              <Box mt={2}>
                {invitesData.results.map((member) => (
                  <MemberRow
                    key={`invite-${member.id}`}
                    member={member}
                    getStarted={getStarted}
                    editable={false}
                  />
                ))}
              </Box>
            )}

            {newMembers.map((member) => {
              const originalIndex = fields.findIndex((f) => f.id === member.id);
              return (
                <Box mt={2} key={member.id}>
                  <MemberRow
                    member={member}
                    index={originalIndex}
                    getStarted={getStarted}
                    editable
                    control={orgForm.control}
                    onRemove={removeMember}
                    errors={orgForm.formState.errors.members?.[originalIndex]}
                  />
                </Box>
              );
            })}

            <Box sx={{ mt: 2 }}>
              <Button
                variant="outlined"
                size="small"
                sx={{
                  borderRadius: getStarted ? 1 : 0.5,
                  minHeight: 32,
                  fontWeight: !getStarted && 500,
                }}
                onClick={addMember}
                disabled={!getStarted && memberToadd?.length > 3}
                startIcon={
                  <SvgColor
                    src="/assets/icons/ic_add.svg"
                    width={15}
                    height={15}
                  />
                }
              >
                {getStarted ? "Add members" : "Add teammate"}
              </Button>
            </Box>
          </Box>

          <LoadingButton
            fullWidth
            type="submit"
            color="primary"
            variant="contained"
            disabled={!IsOrgFormValid}
            loading={isFetchingInitialData || isCreating}
            sx={{
              borderRadius: 0.5,
              mx: getStarted && "auto",
              maxWidth: "100%",
            }}
          >
            {getStarted ? "Save invites" : "Continue setup"}
          </LoadingButton>
        </Stack>
      </form>
    </Box>
  );

  const renderContent = () => {
    switch (activeStep) {
      case 0:
        return (
          <Stack spacing={2} sx={{ width: { xs: "100%", sm: 520 } }}>
            <Box>
              <Typography
                fontWeight={"fontWeightSemiBold"}
                sx={{
                  fontSize: "28px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                What do you want to set up first?
              </Typography>
              <Typography
                variant="body1"
                sx={{
                  color: "text.secondary",
                  mt: 1,
                  maxWidth: 520,
                }}
              >
                Pick a product path. You will see the first action and the full
                setup path next.
              </Typography>
            </Box>

            {renderProductLoopQuickStarts()}
          </Stack>
        );

      case 2:
        if (!isOwner) {
          return null;
        }
        return (
          <Stack spacing={4} maxWidth="440px">
            <Box>
              <Typography
                fontWeight={"fontWeightSemiBold"}
                sx={{
                  fontSize: "28px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                Invite teammates, or continue alone
              </Typography>
              <Typography
                fontWeight={"fontWeightSemiBold"}
                sx={{
                  fontSize: "28px",
                  color: "text.secondary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                This step is optional. Continue setup when you are ready.
              </Typography>
            </Box>
            {renderOrgSetup()}
          </Stack>
        );
      default:
        if (!isOwner) {
          return null;
        }
        return (
          <Stack spacing={2} maxWidth="440px">
            <Box>
              <Typography
                fontWeight={"fontWeightSemiBold"}
                sx={{
                  fontSize: "28px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                Invite teammates, or continue alone
              </Typography>
              <Typography
                fontWeight={"fontWeightSemiBold"}
                sx={{
                  fontSize: "28px",
                  color: "text.secondary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                This step is optional. Continue setup when you are ready.
              </Typography>
            </Box>
            {renderOrgSetup()}
          </Stack>
        );
    }
  };

  if (getStarted) {
    return renderOrgSetup();
  }

  return (
    <>
      <Helmet>
        <title>Choose first setup | FutureAGI</title>
      </Helmet>
      <Box sx={{ width: "100%", minHeight: "100dvh", display: "flex" }}>
        <Box
          sx={{
            width: { xs: "100%", md: "50%" },
            minHeight: "100dvh",
            bgcolor: "background.paper",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            overflowY: "auto",
          }}
        >
          <Box
            sx={{
              maxWidth: "640px",
              width: "100%",
              px: { xs: 3, sm: 6, md: 10 },
              py: { xs: 4, md: "100px" },
              display: "flex",
              flexDirection: "column",
              gap: 2,
              height: "fit-content",
            }}
          >
            {activeStep > 0 ? (
              <DotsStepper
                variant="dots"
                steps={isOwner ? 3 : 2}
                position="static"
                activeStep={activeStep}
              />
            ) : null}
            {renderContent()}
          </Box>
        </Box>

        <Box
          sx={{
            width: "50%",
            minHeight: "100dvh",
            display: { xs: "none", md: "block" },
            backgroundColor: "background.neutral",
          }}
        >
          <SetupOrgSidePanel />
        </Box>
      </Box>
    </>
  );
};

SetupOrganization.propTypes = {
  getStarted: PropTypes.bool,
};

export default SetupOrganization;
