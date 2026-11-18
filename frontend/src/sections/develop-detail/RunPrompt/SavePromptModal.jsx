import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  Typography,
  Box,
} from "@mui/material";
import React, { useEffect } from "react";
import { useForm, Controller } from "react-hook-form";
import Iconify from "src/components/iconify";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const savePromptSchema = z
  .object({
    saveType: z.enum(["new_version", "new_prompt"]),
    promptName: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.saveType === "new_prompt" && !data.promptName?.trim()) {
      ctx.addIssue({
        path: ["promptName"],
        code: z.ZodIssueCode.custom,
        message: "Prompt name is required",
      });
    }
  });

export default function SavePromptModal() {
  const {
    control,
    handleSubmit,
    watch,
    formState: { isValid },
    setValue,
  } = useForm({
    defaultValues: {
      saveType: "new_version",
      promptName: "",
    },
    resolver: zodResolver(savePromptSchema),
    mode: "onChange",
  });

  const saveType = watch("saveType");

  useEffect(() => {
    if (saveType === "new_version") {
      setValue("promptName", "");
    }
  }, [saveType, setValue]);

  const isNewPrompt = saveType === "new_prompt";

  const handleSavePrompt = () => {};

  return (
    <Dialog
      open={true}
      PaperProps={{
        sx: {
          minWidth: "560px",
          borderRadius: "8px",
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        },
      }}
    >
      <DialogTitle sx={{ padding: 0 }}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightSemiBold"}
        >
          Save prompt
        </Typography>
        <IconButton
          // onClick={handleClose}
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>

      <form
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        }}
        onSubmit={handleSubmit(handleSavePrompt)}
      >
        <DialogContent sx={{ padding: 0 }}>
          <FormControl>
            <Controller
              name="saveType"
              control={control}
              render={({ field }) => (
                <RadioGroup
                  {...field}
                  sx={{ display: "flex", flexDirection: "column", gap: "16px" }}
                >
                  <FormControlLabel
                    value="new_version"
                    sx={{ padding: 0, alignItems: "flex-start" }}
                    control={
                      <Radio
                        sx={{
                          padding: 0,
                          paddingLeft: "10px",
                          paddingTop: "4px",
                        }}
                      />
                    }
                    label={
                      <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                        <Typography
                          variant="s1"
                          color="text.primary"
                          fontWeight="fontWeightMedium"
                        >
                          Save as a new version
                        </Typography>
                        <Typography
                          variant="s2"
                          fontWeight="fontWeightRegular"
                          color="text.secondary"
                        >
                          Save your current changes as a new version without
                          affecting the original
                        </Typography>
                      </Stack>
                    }
                  />

                  <FormControlLabel
                    value="new_prompt"
                    sx={{ padding: 0, alignItems: "flex-start" }}
                    control={
                      <Radio
                        sx={{
                          padding: 0,
                          paddingLeft: "10px",
                          paddingTop: "4px",
                        }}
                      />
                    }
                    label={
                      <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                        <Typography
                          variant="s1"
                          color="text.primary"
                          fontWeight="fontWeightMedium"
                        >
                          Save as a new prompt
                        </Typography>
                        <Typography
                          variant="s2"
                          fontWeight="fontWeightRegular"
                          color="text.secondary"
                        >
                          Store this as a separate prompt so you can reuse or
                          modify it later
                        </Typography>
                      </Stack>
                    }
                  />
                </RadioGroup>
              )}
            />
          </FormControl>
          {isNewPrompt && (
            <Box
              sx={{
                ml: "32px",
              }}
            >
              <FormTextFieldV2
                fullWidth
                label={"Add prompt name"}
                fieldName={"promptName"}
                required
                control={control}
                placeholder="Prompt Name"
                size="small"
                sx={{
                  mt: "24px",
                }}
              />
            </Box>
          )}
        </DialogContent>
      </form>
      <DialogActions
        sx={{
          padding: 0,
          display: "flex",
          flexDirection: "row",
          gap: "16px",
          "& button": {
            margin: 0,
          },
        }}
      >
        <Button
          variant="outlined"
          sx={{
            minWidth: "180px",
            "&:hover": {
              borderColor: "divider",
            },
          }}
        >
          <Typography
            variant="s1"
            color="text.secondary"
            fontWeight="fontWeightMedium"
          >
            Cancel
          </Typography>
        </Button>
        <Button
          variant="contained"
          color="primary"
          type="submit"
          sx={{
            minWidth: "180px",
          }}
          disabled={!isValid}
        >
          <Typography
            variant="s1"
            color="white"
            fontWeight="fontWeightSemiBold"
          >
            Save
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
}
