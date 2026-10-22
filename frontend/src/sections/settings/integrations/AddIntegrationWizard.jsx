import { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Step,
  StepLabel,
  Stepper,
  Typography,
  useTheme,
} from "@mui/material";
import { Icon } from "@iconify/react";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import StepPlatformSelect from "./AddIntegrationWizard/StepPlatformSelect";
import StepCredentials from "./AddIntegrationWizard/StepCredentials";
import StepProjectMapping from "./AddIntegrationWizard/StepProjectMapping";
import StepSyncSettings from "./AddIntegrationWizard/StepSyncSettings";
import {
  DEFAULT_LANGFUSE_HOST,
  SKIP_PROJECT_MAPPING_PLATFORMS,
  SKIP_SYNC_SETTINGS_PLATFORMS,
} from "./constants";

const FULL_STEPS = ["Platform", "Credentials", "Project", "Sync Settings"];
const SHORT_STEPS = ["Platform", "Credentials", "Sync Settings"];
const MINIMAL_STEPS = ["Platform", "Credentials"];

function WizardContent({ onClose, initialPlatform, onSelectSlack }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const [activeStep, setActiveStep] = useState(initialPlatform ? 1 : 0);
  const [createdConnectionId, setCreatedConnectionId] = useState(null);
  const [wizardData, setWizardData] = useState({
    platform: initialPlatform || "",
    hostUrl: DEFAULT_LANGFUSE_HOST,
    publicKey: "",
    secretKey: "",
    caCertificate: "",
    credentials: {},
    langfuseProjects: [],
    selectedLangfuseProject: null,
    totalTraces: 0,
    futureAgiProjectId: null,
    newProjectName: "",
    backfillOption: "new_only",
    backfillFromDate: null,
    backfillToDate: null,
    syncIntervalSeconds: 300,
  });

  const skipProjectMapping = SKIP_PROJECT_MAPPING_PLATFORMS.includes(
    wizardData.platform,
  );
  const skipSyncSettings = SKIP_SYNC_SETTINGS_PLATFORMS.includes(
    wizardData.platform,
  );
  const STEPS = skipSyncSettings
    ? MINIMAL_STEPS
    : skipProjectMapping
      ? SHORT_STEPS
      : FULL_STEPS;

  const handleNext = () => setActiveStep((prev) => prev + 1);
  const handleBack = () => setActiveStep((prev) => prev - 1);
  const updateData = (updates) =>
    setWizardData((prev) => ({ ...prev, ...updates }));

  const successStep = STEPS.length; // step after the last real step
  const handleSuccess = (connectionId) => {
    setCreatedConnectionId(connectionId);
    setActiveStep(successStep);
  };

  const isSuccess = activeStep === successStep;

  return (
    <Box
      display="flex"
      flexDirection="column"
      height="100%"
      sx={{
        p: theme.spacing(2),
        backgroundColor: theme.palette.background.paper,
      }}
    >
      {/* Header */}
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        mb={theme.spacing(2)}
      >
        <Typography fontSize={16} fontWeight={600}>
          {isSuccess ? "" : "Add Integration"}
        </Typography>
        <IconButton onClick={onClose} sx={{ p: 0, color: "text.primary" }}>
          <Icon icon="mingcute:close-line" />
        </IconButton>
      </Box>

      {/* Stepper */}
      {!isSuccess && (
        <Stepper activeStep={activeStep} sx={{ mb: theme.spacing(3) }}>
          {STEPS.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>
      )}

      {/* Step content — dynamic based on platform */}
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {activeStep === 0 && (
          <StepPlatformSelect
            data={wizardData}
            onUpdate={updateData}
            onNext={handleNext}
            onSelectSlack={onSelectSlack}
          />
        )}
        {activeStep === 1 && (
          <StepCredentials
            data={wizardData}
            onUpdate={updateData}
            onNext={handleNext}
            onBack={handleBack}
            onSuccess={skipSyncSettings ? handleSuccess : undefined}
          />
        )}
        {!skipProjectMapping && activeStep === 2 && (
          <StepProjectMapping
            data={wizardData}
            onUpdate={updateData}
            onNext={handleNext}
            onBack={handleBack}
          />
        )}
        {!skipSyncSettings && activeStep === (skipProjectMapping ? 2 : 3) && (
          <StepSyncSettings
            data={wizardData}
            onUpdate={updateData}
            onBack={handleBack}
            onSuccess={handleSuccess}
          />
        )}
        {isSuccess && (
          <Box textAlign="center" py={theme.spacing(3)}>
            <Iconify
              icon="solar:check-circle-bold"
              width={64}
              sx={{ color: "success.main", mb: theme.spacing(2) }}
            />
            <Typography
              sx={{
                typography: "m3",
                fontWeight: "fontWeightSemiBold",
                color: "text.primary",
              }}
              gutterBottom
            >
              Integration Connected!
            </Typography>
            <Typography
              sx={{
                typography: "s1",
                color: "text.secondary",
                mb: theme.spacing(3),
              }}
            >
              {skipSyncSettings
                ? "Your integration is now active."
                : "Your integration is now active. Traces will begin syncing shortly."}
            </Typography>
            <Box display="flex" justifyContent="center" gap={theme.spacing(2)}>
              {createdConnectionId && !skipSyncSettings && (
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  onClick={() => {
                    onClose();
                    navigate(
                      paths.dashboard.settings.integrationDetail(
                        createdConnectionId,
                      ),
                    );
                  }}
                  sx={{ fontWeight: 500 }}
                >
                  View Integration
                </Button>
              )}
              <Button
                variant={skipSyncSettings ? "contained" : "outlined"}
                color="primary"
                size="small"
                onClick={() => {
                  onClose();
                  if (skipSyncSettings) return; // just close the drawer
                  navigate(paths.dashboard.observe);
                }}
                sx={{ fontWeight: 500 }}
              >
                {skipSyncSettings ? "Done" : "Go to Project"}
              </Button>
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
}

WizardContent.propTypes = {
  onClose: PropTypes.func.isRequired,
  initialPlatform: PropTypes.string,
  onSelectSlack: PropTypes.func.isRequired,
};

export default function AddIntegrationWizard({
  open,
  onClose,
  initialPlatform,
  onSelectSlack,
}) {
  const theme = useTheme();

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      SlideProps={{ unmountOnExit: true }}
      PaperProps={{
        sx: {
          height: "100vh",
          width: 520,
          position: "fixed",
          zIndex: 10,
          boxShadow: theme.customShadows?.drawer || "-10px 0px 100px #00000035",
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
            borderRadius: "0px !important",
          },
        },
      }}
    >
      <WizardContent onClose={onClose} initialPlatform={initialPlatform} onSelectSlack={onSelectSlack} />
    </Drawer>
  );
}

AddIntegrationWizard.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  initialPlatform: PropTypes.string,
  onSelectSlack: PropTypes.func.isRequired,
};
