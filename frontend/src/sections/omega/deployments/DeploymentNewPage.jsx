import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import { useSearchParams } from "react-router-dom";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { parseHubSource } from "./lib/hubBundle";
import HubSourceGate from "./new/HubSourceGate";
import HubDraftOwner, {
  useHubDraftRef,
  useHubDraftState,
} from "../hub/components/HubDraftOwner";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { projectWords } from "src/sections/omega/shell/switches";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import { deploymentCreateRequest } from "../code/deploymentRequests";
import { useCanManageDeployments } from "../code/manageRole";
import ShowAsCode from "../code/ShowAsCode";
import {
  newIdempotencyKey,
  retryIdempotent,
  useDeployment,
  useDeploymentList,
  useDeploymentMutation,
} from "./api/queries";
import DeploymentErrorState from "./components/DeploymentErrorState";
import DeploymentPreviewNotice from "./components/DeploymentPreviewNotice";
import DeploymentsFrame from "./components/DeploymentsFrame";
import SessionGate, { LoadingRows } from "./components/SessionGate";
import { deploymentNameFrom } from "./lib/bundle";
import { liveRevisionOf } from "./lib/format";
import { deploymentPath, deploymentsPath } from "./lib/links";
import { rowsFromEnv, settingsProblems } from "./lib/settingsForm";
import {
  CreateError,
  DeployButton,
  DeployReview,
  DeployTracker,
} from "./new/DeployStep";
import SettingsStep from "./new/SettingsStep";
import SourceStep from "./new/SourceStep";

const STEPS = ["Source", "Settings", "Deploy"];
const EMPTY_SETTINGS = { name: "", rows: [], secretRefs: [], studio: true };

/** One Idempotency-Key per distinct request: the same request re-sent keeps it. */
function useRequestKey(bundle, settings, env) {
  const fingerprint = JSON.stringify([
    bundle?.contentSha256,
    settings.name,
    Object.entries(env),
    settings.secretRefs,
    settings.studio,
  ]);
  const ref = useHubDraftRef("wizard.requestKey", { fingerprint: null, key: null });
  if (ref.current.fingerprint !== fingerprint) {
    ref.current = { fingerprint, key: newIdempotencyKey("deploy") };
  }
  return ref.current.key;
}

function Wizard({ base, initialBundle = null }) {
  const [step, setStep] = useHubDraftState("wizard.step", 0);
  const [bundle, setBundle] = useHubDraftState("wizard.bundle", initialBundle);
  const [settings, setSettings] = useHubDraftState("wizard.settings", () =>
    base
      ? {
          name: base.name,
          rows: rowsFromEnv(base.env),
          secretRefs: [...(base.secretRefs || [])],
          studio: base.studio !== false,
        }
      : { ...EMPTY_SETTINGS, name: initialBundle ? deploymentNameFrom(initialBundle.manifest.name) : "" },
  );
  const [tracked, setTracked] = useHubDraftState("wizard.tracked", null);
  const canManage = useCanManageDeployments();
  const list = useDeploymentList();
  const checks = useMemo(() => settingsProblems(settings), [settings]);
  const key = useRequestKey(bundle, settings, checks.env);
  const existing =
    (list.data || []).find((d) => d.name === settings.name) || null;

  const create = useDeploymentMutation(
    (api) =>
      api.create(
        {
          name: settings.name,
          bundle,
          env: checks.env,
          secretRefs: settings.secretRefs,
          studio: settings.studio,
        },
        { idempotencyKey: key },
      ),
    {
      retry: retryIdempotent,
      onSuccess: (detail) =>
        setTracked({ id: detail.id, revision: detail.revision }),
    },
  );

  const chooseBundle = (next) => {
    setBundle(next);
    create.reset();
    if (next && !base && !settings.name) {
      setSettings((s) => ({
        ...s,
        name: deploymentNameFrom(next.manifest.name),
      }));
    }
  };

  const canContinue =
    step === 0 ? Boolean(bundle) : step === 1 ? checks.ok : false;
  // What Deploy would send, built only while "Show as code" is open; nothing
  // for a member, whom EE refuses a deploy (manageRole.js).
  const deployRequest = useCallback(
    () => deploymentCreateRequest({ bundle, settings, checks }, { canManage }),
    [bundle, settings, checks, canManage],
  );

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 3, maxWidth: 880 }}
    >
      <Stepper
        activeStep={step}
        sx={{ "& .MuiStepLabel-label": { typography: "s1" } }}
      >
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>
      <Box data-testid={`wizard-step-${STEPS[step].toLowerCase()}`}>
        {step === 0 && (
          <>
            <DeploymentPreviewNotice />
            <SourceStep bundle={bundle} onBundle={chooseBundle} />
          </>
        )}
        {step === 1 && (
          <SettingsStep
            value={settings}
            checks={checks}
            nameLocked={Boolean(base)}
            existing={existing}
            onChange={(next) => {
              setSettings(next);
              create.reset();
            }}
          />
        )}
        {step === 2 &&
          (tracked ? (
            <DeployTracker
              id={tracked.id}
              revision={tracked.revision}
              onEdit={() => {
                setTracked(null);
                create.reset();
                setStep(1);
              }}
            />
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <DeployReview
                bundle={bundle}
                settings={settings}
                env={checks.env}
              />
              <CreateError error={create.error} />
            </Box>
          ))}
      </Box>
      {!tracked && (
        <Box sx={{ display: "flex", justifyContent: "space-between", gap: 1 }}>
          <Button
            color="inherit"
            size="small"
            disabled={step === 0 || create.isPending}
            onClick={() => setStep(step - 1)}
          >
            Back
          </Button>
          {step < 2 ? (
            <Button
              variant="contained"
              size="small"
              disabled={!canContinue}
              onClick={() => setStep(step + 1)}
              data-testid="wizard-continue"
            >
              Continue
            </Button>
          ) : (
            <DeployButton
              onClick={() => create.mutate()}
              pending={create.isPending}
              disabled={!bundle || !checks.ok || !canManage}
            />
          )}
        </Box>
      )}
      {/* One panel for every step, so it stays open and follows each
          change on Source and Settings, below the step's own buttons; it sends the wizard's own key. */}
      {!tracked && (
        <ShowAsCode
          getRequest={deployRequest}
          idempotencyKey={key}
          blockedText={
            canManage
              ? "Choose a project and valid settings to see the request Deploy would send."
              : "Only workspace owners and admins can deploy, so there is no request to show. Ask one of them to deploy this project."
          }
          testId="deploy-show-as-code"
        />
      )}
    </Box>
  );
}

Wizard.propTypes = { base: PropTypes.object, initialBundle: PropTypes.object };

/** The settings a new revision of an existing deployment starts from. */
function baseFrom(detail) {
  const revision = liveRevisionOf(detail) || (detail.revisions || [])[0] || {};
  return {
    name: detail.name,
    env: revision.env || {},
    secretRefs: revision.secretRefs || [],
    studio: revision.studio,
  };
}

function NextRevision({ id }) {
  const query = useDeployment(id);
  if (query.isLoading) return <LoadingRows />;
  if (!query.data)
    return (
      <DeploymentErrorState
        error={query.error}
        onRetry={() => query.refetch()}
      />
    );
  return <Wizard base={baseFrom(query.data)} />;
}

NextRevision.propTypes = { id: PropTypes.string.isRequired };

export default function DeploymentNewPage() {
  const [params] = useSearchParams();
  const { currentWorkspaceId } = useWorkspace();
  const hubSource = useMemo(() => {
    try { return { pin: parseHubSource(params), error: null }; }
    catch (error) { return { pin: null, error }; }
  }, [params]);
  // A conflicting Hub query is an error, never a silent redeploy preference.
  const id = params.has("hub") ? null : params.get("deployment");
  const words = projectWords(useOmegaPart(OMEGA_PART.DEPLOY_LANGGRAPH_JS));
  const frame = (node) => (
    <DeploymentsFrame
      testId="omega-tab-deployment-new"
      title={id ? "Deploy a new revision" : "New deployment"}
      description={
        id
          ? "Upload the project again with the settings this revision should run with."
          : `Deploy ${words.one}: ${words.folder}.`
      }
      back={
        id
          ? { href: deploymentPath(id), label: "Deployment" }
          : { href: deploymentsPath(), label: "Deployments" }
      }
    >
      {node}
    </DeploymentsFrame>
  );
  return (
    // Retain data, not live request-capable children, above session fallback.
    // A new workspace gets a fresh checking gate, never the old ready render.
    <HubDraftOwner key={id ? `${currentWorkspaceId}:${id}` : "new"}>
      <SessionGate key={currentWorkspaceId} fallbackFrame={frame}>
        {frame(id ? <NextRevision key={`${currentWorkspaceId}:${id}`} id={id} /> :
          <HubSourceGate
            hubRef={hubSource.pin?.ref || null}
            error={hubSource.error}
            workspaceId={currentWorkspaceId}
          >
            {(initialBundle, { workspaceChanged }) => !workspaceChanged && (
              <Wizard initialBundle={initialBundle} />
            )}
          </HubSourceGate>
        )}
      </SessionGate>
    </HubDraftOwner>
  );
}
