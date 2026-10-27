import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { DEFAULT_SCENARIOS } from "../../../panels/scenarioCountRules";
import { ROW_BOX_SX, T12 } from "../../drawerStyles";
import useDrawerForm from "../useDrawerForm";
import {
  Field,
  FormCard,
  HintLine,
  Label,
  MONO,
  WhereOption,
  useUiOnlyBuild,
} from "../formBits";
import CodeSnippet from "../shared/CodeSnippet";
import FrameworkTuning from "../shared/FrameworkTuning";
import RailStep from "../shared/RailStep";
import SampleTag from "../shared/SampleTag";
import TextLink from "../shared/TextLink";
import GitHostForm from "./GitHostForm";
import {
  ADAPTERS,
  ADAPTER_ENDPOINT_PLACEHOLDER,
  FRAMEWORK_META,
  OPENAI_AGENT_BUILDER_NOTE,
  TRACEAI_FRAMEWORK_NOTE,
  frameworkTuningOf,
  localInitOf,
  shortSha,
} from "./sampleData";

/* How long the sample local agent takes to check in. A real one checks in when it starts. */
const CHECK_IN_MS = 2500;
const validUrl = (u) => /^(https?|wss?):\/\/\S+$/i.test(String(u || "").trim());

/**
 * A framework agent through our SDK adapter, as Vel's: install it and wrap
 * the graph, pipeline, entry agent or crew in serve(), then say where it's
 * served. Or run it on your own machine: init() instead of serve(), and it
 * checks in with its commit. "Point us at the repository instead" swaps in
 * the repository setup, here in the drawer. UI only: the check-in is a
 * sample, preflight is a sample and nothing builds.
 */
export default function FrameworkForm({ item, onBusyChange }) {
  const [viaRepo, setViaRepo] = useState(false);
  if (viaRepo) {
    return (
      <GitHostForm
        item={item}
        onBusyChange={onBusyChange}
        direct
        onBack={() => setViaRepo(false)}
      />
    );
  }
  return (
    <AdapterSteps
      item={item}
      onBusyChange={onBusyChange}
      onRepo={() => setViaRepo(true)}
    />
  );
}
FrameworkForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
};

function AdapterSteps({ item, onBusyChange, onRepo }) {
  const framework = ADAPTERS[item.framework] ? item.framework : "langgraph";
  const meta = FRAMEWORK_META[framework];
  const adapter = ADAPTERS[framework];
  const ts = meta.lang === "typescript";
  const build = useUiOnlyBuild([
    { label: `${meta.label} adapter`, detail: `Described ${adapter.wraps}` },
    { label: "Structure", detail: `${meta.structure} · read by the adapter` },
  ]);
  const [form, set] = useDrawerForm(
    {
      framework,
      where: "url",
      endpoint: ADAPTER_ENDPOINT_PLACEHOLDER,
      scenarioCount: DEFAULT_SCENARIOS,
    },
    build,
  );
  const { where, endpoint, scenarioCount } = form;
  const local = where === "local";

  // The sample local agent checks in a moment after "Run on your machine".
  const [checkIn, setCheckIn] = useState(null);
  useEffect(() => {
    if (!local || checkIn) return undefined;
    const t = setTimeout(
      () =>
        setCheckIn({
          commit: shortSha(`${framework}:local`),
          fingerprint: shortSha(`${framework}:local:dirty`),
        }),
      CHECK_IN_MS,
    );
    return () => clearTimeout(t);
  }, [local, checkIn, framework]);

  const missing = local
    ? !checkIn
      ? "Waiting for your agent to check in"
      : null
    : !validUrl(endpoint)
      ? "Add where serve() runs"
      : null;
  const installBody = ts
    ? "One package. It pulls in nothing else."
    : "One extra on futureagi. It pulls in nothing else.";

  return (
    <FormCard
      uiOnly
      intro="Wrap your graph, pipeline, agents or crew with our adapter. It serves the agent for simulations; you confirm what it found."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={!missing}
      blockedReason={
        local
          ? "Run your agent so it checks in, then preflight checks it."
          : "Add where serve() runs, then preflight asks the adapter to describe it."
      }
      onRun={build.runPreflight}
      hint={missing}
      onBusyChange={onBusyChange}
    >
      <FrameworkTuning
        tuning={frameworkTuningOf(framework)}
        structure={meta.structure}
      />

      <Box>
        <Label>Where it runs</Label>
        <Box
          role="radiogroup"
          aria-label="Where it runs"
          sx={{
            display: "grid",
            gap: 1,
            mt: 0.75,
            gridTemplateColumns: "1fr 1fr",
          }}
        >
          <WhereOption
            icon="solar:plug-circle-linear"
            label="Served at a URL"
            short="We call serve() where it runs · Deployed by you"
            hint="We can't build it ourselves, so your own coding agent makes changes and we prove them where it's served."
            on={!local}
            onClick={() => set("where")("url")}
          />
          <WhereOption
            icon="solar:laptop-2-linear"
            label="Run on your machine"
            short="Local code through our SDK · identified by commit"
            hint="It reports its commit and uncommitted changes, so we can prove each change."
            on={local}
            onClick={() => set("where")("local")}
          />
        </Box>
      </Box>

      {local ? (
        <Box>
          <RailStep n={1} title="Install our SDK" body={installBody}>
            <CodeSnippet code={adapter.install} />
          </RailStep>
          <RailStep
            n={2}
            title="Add one line where your agent starts"
            body={
              <HintLine
                text="init() leaves your code as it is."
                hint={`It runs our scenarios through ${meta.label}'s own loop on this machine and reports the structure.`}
              />
            }
          >
            <CodeSnippet code={localInitOf(adapter)} />
          </RailStep>
          <RailStep
            n={3}
            title="Run your agent as usual"
            body={
              <HintLine
                text="It checks in from your machine."
                hint="init() dials out with your API key, so there's no port to open."
              />
            }
            last
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ ...ROW_BOX_SX, maxWidth: 480, px: 1.5, py: 1 }}
            >
              <Iconify
                icon={
                  checkIn
                    ? "solar:check-circle-linear"
                    : "eos-icons:three-dots-loading"
                }
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography
                component="div"
                role="status"
                sx={{
                  ...T12,
                  flex: 1,
                  color: checkIn ? "text.primary" : "text.secondary",
                }}
              >
                {checkIn ? (
                  <HintLine
                    text={
                      <>
                        {"Connected · commit "}
                        <Box component="span" sx={{ fontFamily: MONO }}>
                          {checkIn.commit}
                        </Box>
                        {" + uncommitted changes"}
                      </>
                    }
                    hint={`Fingerprint of uncommitted changes ${checkIn.fingerprint}. Every run says which commit and changes it ran.`}
                  />
                ) : (
                  "Waiting for your agent to check in…"
                )}
              </Typography>
              {checkIn && <SampleTag />}
            </Stack>
          </RailStep>
        </Box>
      ) : (
        <Box>
          <RailStep n={1} title="Install the adapter" body={installBody}>
            <CodeSnippet code={adapter.install} />
          </RailStep>
          <RailStep
            n={2}
            title={`Wrap ${adapter.wraps}`}
            body={
              <HintLine
                text="serve() leaves your code as it is."
                hint={`It runs conversations through ${meta.label}'s own loop and reports the structure.`}
              />
            }
          >
            <CodeSnippet code={adapter.code} />
          </RailStep>
          <RailStep
            n={3}
            title="Run it and tell us where"
            body={
              <HintLine
                text="Local works, with no port to open. Deployed? Use that URL."
                hint="serve() dials out with your API key, so local works while it's running."
              />
            }
            last
          >
            <Box sx={{ maxWidth: 480 }}>
              <CodeSnippet code={adapter.run} />
              <Field
                label="Adapter endpoint"
                required
                mono
                value={endpoint}
                onChange={set("endpoint")}
                placeholder={ADAPTER_ENDPOINT_PLACEHOLDER}
                error={
                  endpoint.trim() && !validUrl(endpoint)
                    ? "An address starts with https://, http://, wss:// or ws://"
                    : null
                }
                sx={{ mt: 1.25 }}
              />
            </Box>
          </RailStep>
        </Box>
      )}

      <Typography sx={{ ...T12, color: "text.secondary" }}>
        {"Code already in GitHub, GitLab or Bitbucket? "}
        <TextLink onClick={onRepo} sx={{ display: "inline-flex" }}>
          Point us at the repository instead
        </TextLink>
      </Typography>
      <Box
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: "6px",
          bgcolor: "background.neutral",
        }}
      >
        <Typography component="div" sx={{ ...T12, color: "text.secondary" }}>
          <HintLine
            text="OpenAI Agent Builder is shutting down."
            hint={`${OPENAI_AGENT_BUILDER_NOTE} ${TRACEAI_FRAMEWORK_NOTE}`}
          />
        </Typography>
      </Box>
    </FormCard>
  );
}
AdapterSteps.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
  onRepo: PropTypes.func.isRequired,
};
