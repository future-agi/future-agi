import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import PersonaBasicInfo from "src/sections/persona/PersonaCreateEdit/PersonaBasicInfo";
import PersonaBehavioralSetting from "src/sections/persona/PersonaCreateEdit/PersonaBehaviouralSetting";
import PersonaConversationSetting from "src/sections/persona/PersonaCreateEdit/PersonaConversationSetting";
import { objectSnakeToCamel } from "src/utils/utils";
import {
  personEditDefaultValue,
  PersonEditValidationSchema,
} from "./editHelper";
import { zodResolver } from "@hookform/resolvers/zod";
import { enqueueSnackbar } from "src/components/snackbar";
import { ShowComponent } from "src/components/show";
import { PersonaChatSettings } from "src/sections/persona/PersonaCreateEdit/PersonaChatSettings";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const EditPersonaForm = ({
  editPersona,
  onClose,
  onCellValueChanged,
  params,
  simulationType,
}) => {
  const form = useForm({
    defaultValues: personEditDefaultValue(
      objectSnakeToCamel({ ...editPersona, simulationType } || {}),
    ),
    resolver: zodResolver(PersonEditValidationSchema),
  });
  const onSubmit = (data) => {
    if (editPersona?.name) {
      data["name"] = editPersona?.name;
    }
    delete data?.simulationType;

    onCellValueChanged({
      ...params,
      newValue: JSON.stringify(data),
      onSuccess: () => {
        enqueueSnackbar("Persona has been updated", { variant: "success" });
      },
    });
    onClose();
  };
  return (
    <Box sx={{ height: "100%" }}>
      <FormProvider {...form}>
        <Box
          sx={{
            padding: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            height: "100%",
          }}
          component="form"
          onSubmit={form.handleSubmit(onSubmit)}
        >
          {/* <PrintFormValues control={form.control} /> */}
          <Box>
            <Typography typography="m2" fontWeight="fontWeightSemiBold">
              Edit persona
            </Typography>
          </Box>
          <Divider flexItem orientation="horizontal" />
          <Box
            sx={{
              flex: 1,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            <PersonaBasicInfo
              viewOptions={{
                name: false,
                description: false,
              }}
              multiple={false}
            />
            <PersonaBehavioralSetting type={simulationType} multiple={false} />

            <ShowComponent condition={simulationType === AGENT_TYPES.VOICE}>
              <PersonaConversationSetting multiple={false} />
            </ShowComponent>
            <ShowComponent condition={simulationType === AGENT_TYPES.CHAT}>
              <PersonaChatSettings multiple={false} />
            </ShowComponent>
          </Box>
          <Box sx={{ display: "flex", gap: 2 }}>
            <Button
              variant="outlined"
              color="primary"
              size="small"
              fullWidth
              onClick={onClose}
            >
              Cancel
            </Button>
            <LoadingButton
              variant="contained"
              color="primary"
              size="small"
              fullWidth
              type="submit"
            >
              Update
            </LoadingButton>
          </Box>
        </Box>
      </FormProvider>
    </Box>
  );
};

EditPersonaForm.propTypes = {
  editPersona: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
  params: PropTypes.object,
  simulationType: PropTypes.string,
};

const EditPersona = ({
  open,
  onClose,
  editPersona,
  onCellValueChanged,
  params,
}) => {
  const simulationType =
    params?.colDef?.col?.metadata?.simulationType ?? AGENT_TYPES.VOICE;
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
    >
      <Box sx={{ width: "700px", height: "100vh", position: "relative" }}>
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
        {simulationType && (
          <EditPersonaForm
            editPersona={editPersona}
            onClose={onClose}
            onCellValueChanged={onCellValueChanged}
            params={params}
            simulationType={simulationType}
          />
        )}
      </Box>
    </Drawer>
  );
};

EditPersona.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  editPersona: PropTypes.object,
  onCellValueChanged: PropTypes.func,
  params: PropTypes.object,
  simulationType: PropTypes.string,
};

export default EditPersona;
