import PropTypes from "prop-types";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Box, Drawer, IconButton, Stack, Typography, CircularProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import { getEval, systemEvalFor, simulationPreviewData } from "../../_mock/evals";
import { getAgentType } from "../../_mock/agentTypes";

/**
 * Edit an added eval — the pencil next to the bin.
 *
 * Opens the eval library's own config screen in edit mode (instructions,
 * model, output type, error localization, variable mapping against the
 * simulation preview, "Update Evaluation"), exactly as the legacy run setup
 * does.
 *
 * That screen loads the eval by template id. Evals added from the library
 * already carry one; a preset names the system eval it stands for, so it is
 * looked up in the library first and the id remembered on the entry.
 */
const isLibraryEval = (item) =>
  !!item?.custom && !getEval(item.id) && item.evalKind !== "twin_end_state";

export const canEditEval = (item) =>
  !!item && (!!item.templateId || isLibraryEval(item) || !!systemEvalFor(item.id));

export default function EditEvalDrawer({ item, env, envState, onClose, onSave }) {
  const knownId = item.templateId || (isLibraryEval(item) ? item.id : null);
  const systemName = knownId ? null : systemEvalFor(item.id);

  /* The template list, not the dataset-scoped get_evals_list: that route
     only takes a dataset UUID, and an environment id isn't one. */
  const lookup = useQuery({
    queryKey: ["simulate-v2", "system-eval", systemName],
    queryFn: async () => {
      const { data } = await axios.post(endpoints.develop.eval.listEvalTemplates, {
        page: 0,
        page_size: 25,
        search: systemName,
        owner_filter: "all",
      });
      const items = data?.result?.items || [];
      return items.find((e) => e.name === systemName && e.owner === "system")
        || items.find((e) => e.name === systemName)
        || null;
    },
    enabled: !!systemName,
    staleTime: 5 * 60 * 1000,
  });

  const previewData = useMemo(
    () => simulationPreviewData(env, envState, getAgentType(envState?.agent?.typeId)),
    [env, envState],
  );

  const templateId = knownId || lookup.data?.id || null;

  if (!templateId) {
    return (
      <LookupDrawer
        onClose={onClose}
        name={item.name}
        systemName={systemName}
        failed={!systemName || lookup.isError || (lookup.isSuccess && !lookup.data)}
      />
    );
  }

  return (
    <EvalPickerDrawer
      open
      onClose={onClose}
      paperSx={{ backgroundColor: "background.paper", backgroundImage: "none" }}
      source="create-simulate"
      sourcePreviewData={previewData}
      existingEvals={[]}
      initialEval={{
        id: templateId,
        template_id: templateId,
        name: item.instanceName || item.name,
        /* A preset's default mapping uses the prototype's shorthand, not the
           run's real columns — start it clean so the picker's own column list
           fills it. Once configured here, the saved mapping comes back. */
        mapping: knownId ? item.mapping || {} : {},
        config: item.config || {},
        run_config: item.runConfig || {},
        ...(item.model && { model: item.model }),
      }}
      showVersionControls
      onEvalAdded={(config) => {
        /* The row keeps its name and slot; what changes is the config the
           run scores with. The picker closes itself after this. */
        onSave({
          templateId,
          instanceName: config.name || item.instanceName,
          mapping: config.mapping || item.mapping,
          model: config.model ?? item.model,
          config: config.config || item.config,
          runConfig: {
            agent_mode: config.agent_mode,
            check_internet: config.check_internet,
            summary: config.summary,
            tools: config.tools,
            knowledge_bases: config.knowledge_bases,
            data_injection: config.data_injection,
            error_localizer_enabled: config.error_localizer_enabled,
          },
          ...(config.outputType !== "pass_fail" && typeof config.pass_threshold === "number"
            && { threshold: config.pass_threshold }),
        });
      }}
    />
  );
}

EditEvalDrawer.propTypes = {
  item: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
};

/* Same surface and width as the config screen it hands over to, so nothing
   jumps when the template arrives. */
function LookupDrawer({ onClose, name, systemName, failed }) {
  return (
    <Drawer
      anchor="right"
      open
      onClose={onClose}
      PaperProps={{ sx: { width: "90vw", maxWidth: "95vw", height: "100vh", borderRadius: "0px !important" } }}
      ModalProps={{ BackdropProps: { style: { backgroundColor: "transparent" } } }}
      sx={{ "&& .MuiDrawer-paper": { backgroundColor: "background.paper", backgroundImage: "none" } }}
    >
      <Stack sx={{ height: "100%", p: 2.5 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <IconButton size="small" onClick={onClose} aria-label="Back">
            <Iconify icon="mingcute:arrow-left-line" width={18} />
          </IconButton>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>{name}</Typography>
        </Stack>
        <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ flex: 1 }}>
          {failed ? (
            <>
              <Iconify icon="mdi:alert-circle-outline" width={36} sx={{ color: "text.disabled" }} />
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>Couldn&apos;t open this evaluation</Typography>
              <Box sx={{ maxWidth: 440, textAlign: "center" }}>
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {systemName
                    ? `${systemName} wasn't found in the eval library. Check you're signed in to a workspace with system evals, then try again.`
                    : "This evaluation has no library template to configure."}
                </Typography>
              </Box>
            </>
          ) : (
            <>
              <CircularProgress size={24} />
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Loading evaluation…</Typography>
            </>
          )}
        </Stack>
      </Stack>
    </Drawer>
  );
}

LookupDrawer.propTypes = {
  onClose: PropTypes.func,
  name: PropTypes.string,
  systemName: PropTypes.string,
  failed: PropTypes.bool,
};
