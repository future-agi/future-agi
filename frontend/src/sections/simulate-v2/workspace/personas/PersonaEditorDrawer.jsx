import PropTypes from "prop-types";
import { useEffect } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { Box, Stack, Typography, IconButton, Button, Divider } from "@mui/material";
import Iconify from "src/components/iconify";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PersonaBasicInfo from "src/sections/persona/PersonaCreateEdit/PersonaBasicInfo";
import PersonaBehavioralSetting from "src/sections/persona/PersonaCreateEdit/PersonaBehaviouralSetting";
import PersonaConversationSetting from "src/sections/persona/PersonaCreateEdit/PersonaConversationSetting";
import { PersonaChatSettings } from "src/sections/persona/PersonaCreateEdit/PersonaChatSettings";
import { getPersonaDefaultValues } from "src/sections/persona/PersonaCreateEdit/common";
import SideDrawer from "../../components/SideDrawer";
import PersonaDeliverySection from "./PersonaDeliverySection";
import { defaultFidelity } from "../../_mock/fidelity";
import { PERSONA_KINDS } from "../../_mock/personas";
import { effectiveModality } from "../../_mock/rlContract";

/**
 * Create or edit a persona.
 *
 * This is the app's own persona form, not a second one that looks like it.
 * `PersonaBasicInfo`, `PersonaBehavioralSetting`, `PersonaConversationSetting`
 * and `PersonaChatSettings` are all driven by react-hook-form context and know
 * nothing about the network, so they drop straight into a FormProvider here.
 * Only the submit differs: Personas writes to the API, this writes into the
 * environment's own state.
 *
 * Which sections appear follows the modality already in force, the same
 * resolution the RL contract uses — a voice environment gets accent,
 * multilingual, conversation speed and interruption sensitivity, and a chat
 * environment gets tone, verbosity, typos and emoji instead. Neither is a
 * choice to make twice: the environment already knows what its agent is.
 */
export default function PersonaEditorDrawer({ persona, env, envState, onClose, onSave }) {
  const existing = !!persona?.id;
  const modality = effectiveModality(env, envState);
  const type = modality === "voice" ? AGENT_TYPES.VOICE : AGENT_TYPES.CHAT;
  const voice = type === AGENT_TYPES.VOICE;

  const form = useForm({
    defaultValues: { ...getPersonaDefaultValues(), simulationType: type },
  });

  /* Reopening on a different persona must not carry the last one's answers. */
  const { reset } = form;
  useEffect(() => {
    if (!persona) return;
    reset({
      ...getPersonaDefaultValues(),
      simulationType: type,
      name: persona.name || "",
      description: persona.blurb || "",
      personality: (persona.traits || []).map((value) => ({ value })),
      /*
        Each persona owns its own line — noise, barge-in, typos, whatever the
        modality's channel dictates. Missing fields fall back to the environment
        defaults so an untouched persona still has a defined delivery on record.
      */
      delivery: { ...defaultFidelity(env), ...(persona.delivery || {}) },
    });
  }, [persona, type, reset, env]);

  const kind = PERSONA_KINDS.find((k) => k.id === (persona?.kind || "persona"));

  const submit = form.handleSubmit((values) => {
    onSave?.({
      ...persona,
      name: values.name,
      blurb: values.description,
      traits: (values.personality || []).map((p) => p.value),
      modalities: [modality],
      delivery: values.delivery,
    });
    onClose();
  });

  return (
    <SideDrawer open={!!persona} onClose={onClose} width={700}>
      {persona && (
        <FormProvider {...form}>
          <Stack sx={{ height: "100%" }}>
            <Stack
              direction="row" alignItems="flex-start" spacing={2}
              sx={{ px: 2.5, py: 2, flexShrink: 0 }}
            >
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "m2", fontWeight: 600 }}>
                  {existing ? `Edit ${persona.name}` : `Create ${kind.label.toLowerCase()}`}
                </Typography>
                <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                  {existing
                    ? `Editing ${persona.version} — saving creates a new version`
                    : `Create custom personas for more realistic ${voice ? "calls" : "chats"}`}
                </Typography>
              </Box>
              <IconButton size="small" onClick={onClose}>
                <Iconify icon="akar-icons:cross" width={16} sx={{ color: "text.primary" }} />
              </IconButton>
            </Stack>
            <Divider />

            <Box sx={{ flex: 1, overflowY: "auto", p: 2 }}>
              <Stack spacing={2}>
                <PersonaBasicInfo />
                <PersonaBehavioralSetting type={type} showClearButton />
                {voice ? <PersonaConversationSetting showClearButton /> : <PersonaChatSettings />}
                <PersonaDeliverySection env={env} envState={envState} />
              </Stack>
            </Box>

            <Divider />
            <Stack
              direction="row" spacing={1.5}
              sx={{ px: 2.5, py: 2, flexShrink: 0 }}
            >
              <Button
                fullWidth variant="outlined" color="inherit" onClick={onClose}
                sx={{ typography: "s2", fontWeight: 600, borderColor: "divider" }}
              >
                Cancel
              </Button>
              <Button
                fullWidth variant="contained" color="primary" onClick={submit}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                Save
              </Button>
            </Stack>
          </Stack>
        </FormProvider>
      )}
    </SideDrawer>
  );
}

PersonaEditorDrawer.propTypes = {
  persona: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
};
