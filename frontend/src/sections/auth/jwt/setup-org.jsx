import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  IconButton,
  styled,
  MobileStepper,
  Button,
  Alert,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import axios, { endpoints } from "src/utils/axios";
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import PropTypes from "prop-types";
import { paths } from "src/routes/paths";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import RightSectionAuth from "./RightSectionAuth";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import { Controller, useForm, useFieldArray, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { useAuthContext } from "src/auth/hooks";
import { DEFAULT_ROLES, ROLE_OPTIONS } from "./constants";
import { organizationSchema } from "./zodSchema";
import OnboardingProfileForm from "./onboarding-profile-form";
import { generateNameFromEmail, redirectAfterOrgSetup } from "./common";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";
import { useSearchParams } from "react-router-dom";

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
          direction="row"
          alignItems="center"
          sx={{
            position: "relative",
            gap: 2,
            width: "100%",
            pr: 5,
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
        direction="row"
        alignItems="flex-start"
        sx={{
          position: "relative",
          gap: 2,
          width: "100%",
          pr: 5,
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
              sx={{ flex: 1.5, minWidth: 110, borderRadius: "4px" }}
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

const placeholderMembers = (seed) =>
  seed
    ? [{ email: "", name: "", organization_role: "Owner", disabled: false }]
    : [];

// Never seed on self-hosted: the member block is hidden there, so a seeded row
// with an empty email fails validation with nothing on screen to fix.
const useOrganizationInitialData = (isOwner, seedPlaceholderMember = true) => {
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
            : placeholderMembers(seedPlaceholderMember);

        setInitialData({
          orgName: orgDetails.org_name,
          members: prefilledMembers,
        });
      } catch (error) {
        enqueueSnackbar("Failed to fetch organization details", {
          variant: "error",
        });
        setInitialData({
          orgName: "",
          members: placeholderMembers(seedPlaceholderMember),
        });
      } finally {
        setIsLoading(false);
      }
    };
    if (!isOwner) {
      return;
    }

    fetchOrgDetails();
  }, [enqueueSnackbar]);

  return { initialData, isLoading };
};

const SetupOrganization = ({ getStarted = false }) => {
  const queryClient = useQueryClient();
  const [inviteEmailWarnings, setInviteEmailWarnings] = useState([]);
  const [hasInviteErrors, setHasInviteErrors] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeStep = parseInt(searchParams.get("step") || "0", 10);
  // Where to land once org setup finishes — honor an internal `returnTo`
  // persisted in localStorage over the default dashboard page.
  const returnTo = (() => {
    const rt = localStorage.getItem("redirectUrl");
    return rt && rt.startsWith("/") && !rt.startsWith("//") ? rt : null;
  })();

  const setActiveStep = useCallback(
    (newStep) => {
      const params = new URLSearchParams(searchParams);
      params.set("step", newStep.toString());
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const { enqueueSnackbar } = useSnackbar();
  const { user } = useAuthContext();
  const isOwner = user?.organization_role === "Owner";
  // Confirmed reads only — a failed deployment-info probe must not hide the
  // invite step from a cloud user.
  const { isOSS: ossMode, isSuccess: modeConfirmed } = useDeploymentMode();
  const isOSS = modeConfirmed && ossMode;
  const { initialData, isLoading: isFetchingInitialData } =
    useOrganizationInitialData(isOwner, !isOSS);

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

  const {
    data: userOnboardingData,
    isError: isOnboardingError,
    refetch: refetchOnboarding,
  } = useQuery({
    queryKey: ["user-role-goals"],
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.auth.user_onboarding_info, {
        signal,
      });
      return response;
    },
    enabled: !getStarted,
    select: (d) => d?.data?.result,
    // The step shows its own error state with Retry instead of a toast.
    meta: { errorHandled: true },
  });

  const { mutate: saveUserData, isPending: isSavingUserData } = useMutation({
    mutationFn: async (data) => {
      return await axios.post(endpoints.auth.user_onboarding_info, data);
    },
    meta: {
      errorHandled: true,
    },
    onSuccess: async (data, variables) => {
      // An in-flight read must not overwrite the saved profile on a later mount.
      await queryClient.cancelQueries({ queryKey: ["user-role-goals"] });
      const savedProfile = data.data.result.data;
      queryClient.setQueryData(["user-role-goals"], {
        data: {
          result: {
            ...savedProfile,
            completed: Boolean(savedProfile.role && savedProfile.goals.length),
          },
        },
      });
      enqueueSnackbar("Profile updated successfully", { variant: "success" });
      const provider = localStorage.getItem("signupProvider");
      // Only a listed role is sent by name; any other role is reported as a flag.
      const roleIsCustom = !DEFAULT_ROLES.includes(variables?.role);
      trackEvent(Events.signUpCompleted, {
        [PropertyName.email]: user?.email,
        ...(roleIsCustom ? {} : { [PropertyName.role]: variables?.role }),
        role_is_custom: roleIsCustom,
        [PropertyName.goals]: variables?.goals,
        [PropertyName.method]: provider,
      });
      localStorage.removeItem("signupProvider");
      if (isOwner) {
        setActiveStep(2);
      } else {
        // Non-owners (invited users) should go straight to dashboard
        // They're already part of an org, no need for additional onboarding
        if (returnTo) {
          localStorage.setItem("initial-render", "done");
          localStorage.removeItem("redirectUrl");
        }
        window.location.href = returnTo || paths.dashboard.develop;
      }
    },
    onError: (error) => {
      enqueueSnackbar(error?.message || "Failed to save profile", {
        variant: "error",
      });
    },
  });
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

  const addMember = useCallback(() => {
    append({
      email: "",
      name: "",
      organization_role: "Member",
      disabled: false,
    });
  }, [append, getStarted, memberToadd?.length, enqueueSnackbar]);

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

  const continueToDashboard = () => {
    if (returnTo) {
      localStorage.setItem("initial-render", "done");
      localStorage.removeItem("redirectUrl");
    }
    redirectAfterOrgSetup(returnTo || paths.dashboard.getstarted);
  };

  const mergeInviteWarnings = (members = []) =>
    Array.from(
      new Map(
        [
          ...inviteEmailWarnings,
          ...members.filter((member) => member.invite_email_warning),
        ].map((member) => [member.id, member]),
      ).values(),
    );

  const { mutate: createOrg, isPending: isCreating } = useMutation({
    mutationFn: async (data) => {
      const membersToSend = data.members
        .map((m, i) => ({ ...m, originalIndex: i }))
        .filter((m) => !m.disabled && m.email.trim())
        .map((m) => ({
          index: m.originalIndex,
          email: m.email,
          name: m.name || generateNameFromEmail(m.email),
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
        const members = response.data?.result?.created_members || [];
        if (mergeInviteWarnings(members).length === 0) {
          const message =
            members.length === 0
              ? "Organization Setup Successfully."
              : "Invite sent successfully.";
          enqueueSnackbar(message, { variant: "success" });
        }
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

    onSuccess: (response, variables) => {
      refetchInvites();
      const membersToTrack = variables?.members?.filter(
        (m) => !m?.disabled && m?.email?.trim(),
      );

      trackEvent(Events.setupOrganizationClicked, {
        [PropertyName.click]: true,
        members_added: membersToTrack.length,
        roles_assigned: [
          ...new Set(membersToTrack.map((m) => m?.organization_role)),
        ],
      });

      trackEvent(Events.pageView, {
        [PropertyName.click]: {
          members_added: membersToTrack?.length,
          roles_assigned: [
            ...new Set(membersToTrack.map((m) => m?.organization_role)),
          ],
          create_org_clicked: true,
        },
        [PropertyName.count]: membersToTrack.length,
      });

      orgForm.reset();

      setHasInviteErrors(false);
      const warnings = mergeInviteWarnings(
        response.data?.result?.created_members,
      );
      if (warnings.length > 0) {
        // A hard redirect would erase a toast before the user can act on it.
        setInviteEmailWarnings(warnings);
      } else if (!getStarted) {
        continueToDashboard();
      }
    },

    onError: (error) => {
      const result = error?.result;
      const apiErrors = result?.errors || [];
      const members = orgForm.getValues("members");
      setInviteEmailWarnings(mergeInviteWarnings(result?.created_members));
      setHasInviteErrors(true);
      if (!apiErrors.length) {
        enqueueSnackbar(result?.message || result || "Something went wrong.", {
          variant: "error",
        });
        return;
      }

      const hasMemberErrors = apiErrors.some(
        (err) => err?.email || typeof err?.index === "number",
      );

      if (hasMemberErrors) {
        queryClient.invalidateQueries({ queryKey: ["owner-org-invites"] });

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

        enqueueSnackbar(
          result?.message ||
            "Some members could not be added. Correct the errors and try again.",
          { variant: "error" },
        );
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
        name: member.name || generateNameFromEmail(member.email),
      })),
    };
    createOrg(processedData);
  });
  const renderInviteWarnings = () => (
    <Stack spacing={2}>
      <Typography variant="subtitle1">Members added</Typography>
      {inviteEmailWarnings.map((member) => (
        <Alert severity="warning" key={member.id}>
          <strong>{member.email}</strong>: {member.invite_email_warning}
        </Alert>
      ))}
      {!hasInviteErrors && (
        <Button
          variant="contained"
          onClick={
            getStarted ? () => setInviteEmailWarnings([]) : continueToDashboard
          }
        >
          {getStarted ? "Done" : "Continue to dashboard"}
        </Button>
      )}
    </Stack>
  );

  const renderOrgSetup = () => (
    <Box>
      <form onSubmit={handleOrgSubmit} style={{ width: "100%" }}>
        <Stack spacing={2} sx={{ mx: "auto", pb: getStarted ? "16px" : "0" }}>
          {hasInviteErrors &&
            inviteEmailWarnings.length > 0 &&
            renderInviteWarnings()}
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
                  borderRadius: "4px",
                  bgcolor: "background.paper",
                },
              }}
            />
          )}

          {/* Hidden on self-hosted: invites need email delivery, so teammates
              are added later from inside the product. */}
          {!isOSS && (
            <>
              <Typography variant="m2" fontWeight="fontWeightMedium">
                Invite people to collaborate in FutureAGI
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
                  const originalIndex = fields.findIndex(
                    (f) => f.id === member.id,
                  );
                  return (
                    <Box mt={2} key={member.id}>
                      <MemberRow
                        member={member}
                        index={originalIndex}
                        getStarted={getStarted}
                        editable
                        control={orgForm.control}
                        onRemove={removeMember}
                        errors={
                          orgForm.formState.errors.members?.[originalIndex]
                        }
                      />
                    </Box>
                  );
                })}

                <Box sx={{ mt: 2 }}>
                  <Typography
                    variant={getStarted ? "body2" : "s1.2"}
                    sx={{
                      color: "primary.main",
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      border: "1px solid",
                      borderColor: "primary.main",
                      borderRadius: getStarted ? 1 : 0.5,
                      padding: "4px 10px",
                      transition: "all 0.2s",
                      fontWeight: !getStarted && 500,
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                    onClick={addMember}
                    disable={!getStarted && memberToadd?.length > 3}
                  >
                    <SvgColor
                      src="/assets/icons/ic_add.svg"
                      width={15}
                      height={15}
                      sx={{ mr: 1 }}
                    />

                    {getStarted ? "Add members" : "Add"}
                  </Typography>
                </Box>
              </Box>
            </>
          )}

          <LoadingButton
            size="large"
            fullWidth
            type="submit"
            color="primary"
            variant="contained"
            disabled={!IsOrgFormValid}
            loading={isFetchingInitialData || isCreating}
            sx={{
              mx: getStarted && "auto",
              maxWidth: "100%",
            }}
          >
            Continue
          </LoadingButton>

          {/* {!getStarted && (
            <Typography
              textAlign="center"
              onClick={() => {
                router.push(paths.dashboard.getstarted);
              }}
              sx={{
                cursor: isCreating ? "not-allowed" : "pointer",
                opacity: isCreating ? 0.6 : 1,
                width: "414px",
              }}
              color="primary.main"
              fontSize={"12px"}
              fontWeight="fontWeightMedium"
            >
              Skip for now
            </Typography>
          )} */}
        </Stack>
      </form>
    </Box>
  );

  const renderContent = () => {
    switch (activeStep) {
      case 0:
      case 1:
        return null;

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
                  fontSize: "24px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                Collaborate with your team
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
                Make the collaboration seamless
              </Typography>
            </Box>
            {inviteEmailWarnings.length > 0 && !hasInviteErrors
              ? renderInviteWarnings()
              : renderOrgSetup()}
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
                  fontSize: "24px",
                  color: "text.primary",
                  fontFamily: "Inter",
                  lineHeight: "36px",
                }}
              >
                Collaborate with your team
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
                Make the collaboration seamless
              </Typography>
            </Box>
            {inviteEmailWarnings.length > 0 && !hasInviteErrors
              ? renderInviteWarnings()
              : renderOrgSetup()}
          </Stack>
        );
    }
  };

  if (getStarted) {
    return inviteEmailWarnings.length > 0 && !hasInviteErrors
      ? renderInviteWarnings()
      : renderOrgSetup();
  }

  return (
    <Box sx={{ width: "100%", height: "100vh", display: "flex" }}>
      <Box
        sx={{
          width: "50%",
          height: "100vh",
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
            px: 10,
            paddingY: "100px",
            display: "flex",
            flexDirection: "column",
            gap: 2,
            height: "fit-content",
          }}
        >
          <DotsStepper
            variant="dots"
            steps={isOwner ? 3 : 2}
            position="static"
            activeStep={activeStep}
          />
          {userOnboardingData ? (
            <OnboardingProfileForm
              initialData={userOnboardingData}
              activeStep={activeStep}
              setActiveStep={setActiveStep}
              saveUserData={saveUserData}
              isSavingUserData={isSavingUserData}
            />
          ) : (
            (activeStep === 0 || activeStep === 1) &&
            (isOnboardingError ? (
              <ErrorState
                title="Couldn't load your profile"
                detail="Retry to choose your role and goals."
                onRetry={() => refetchOnboarding()}
              />
            ) : (
              <ContentSkeleton variant="panel" label="Loading your profile" />
            ))
          )}
          {renderContent()}
        </Box>
      </Box>

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
};

SetupOrganization.propTypes = {
  getStarted: PropTypes.bool,
};

export default SetupOrganization;
