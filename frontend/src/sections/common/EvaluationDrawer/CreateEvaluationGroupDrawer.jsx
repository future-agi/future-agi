import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "../../../components/svg-color";
import Iconify from "../../../components/iconify";
import CardWrapper from "../../projects/Alerts/components/CardWrapper";
import FormTextFieldV2 from "../../../components/FormTextField/FormTextFieldV2";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useEvalStore } from "../../evals/store/useEvalStore";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "../../../utils/axios";
import { enqueueSnackbar } from "notistack";
import { LoadingButton } from "@mui/lab";
import { useEvaluationContext } from "./context/EvaluationContext";
import { getUniqueEvalRequiredKeys } from "./common";
import { ShowComponent } from "../../../components/show";
import { useNavigate } from "react-router";
import { useSearchParams } from "react-router-dom";

const EvalCard = ({ name, description, onRemove }) => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        padding: theme.spacing(2, 1.5),
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(0.25),
        flexShrink: 0, // Prevent card from shrinking
      }}
    >
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          {name}
        </Typography>
        <IconButton
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: theme.spacing(0.25),
          }}
          onClick={onRemove}
        >
          <SvgColor
            sx={{
              color: "text.disabled",
              height: "12px",
              width: "12px",
            }}
            src={"/assets/icons/ic_delete.svg"}
          />
        </IconButton>
      </Stack>
      <Typography
        typography={"s2"}
        fontWeight={"fontWeightRegular"}
        color={"text.secondary"}
      >
        {description}
      </Typography>
    </Box>
  );
};

EvalCard.propTypes = {
  name: PropTypes.string,
  description: PropTypes.string,
  id: PropTypes.string,
  onRemove: PropTypes.func,
};

const formSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
});

export default function CreateEvaluationGroupDrawer({
  open,
  handleClose,
  isEvalsView = true,
  onBack,
}) {
  const theme = useTheme();
  const [activeButton, setActiveButton] = useState(null);
  const {
    // setPlaygroundEvaluation,
    setVisibleSection,
    setSelectedEval,
    setActionButtonConfig,
    setCurrentTab,
    setSelectedGroup,
  } = useEvaluationContext();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParam] = useSearchParams();
  const initialGroupData = useRef(null);
  const groupId = searchParams.get("groupId");
  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid },
    getValues,
  } = useForm({
    defaultValues: {
      name: "",
      description: "",
    },
    resolver: zodResolver(formSchema),
  });

  const {
    selectedEvals,
    setSelectedEvals,
    setOpenCreateGroupDrawer,
    setCreateGroupMode,
  } = useEvalStore();

  const { data: groupData } = useQuery({
    queryKey: ["group-evals", groupId],
    queryFn: () => axios.get(`${endpoints.develop.eval.groupEvals}${groupId}/`),
    enabled: Boolean(groupId),
    select: (d) => d.data,
  });

  const { mutate, isPending } = useMutation({
    mutationFn: async (payload) => {
      return axios.post(endpoints.develop.eval.groupEvals, payload.data);
    },
    onSuccess: (data, variables) => {
      const response = data?.data?.result;
      if (variables?.test === true) {
        // setPlaygroundEvaluation({
        //   id: "c664aae8-d959-4a9b-8e7c-8ddd0b558fd4",
        //   name: "TEST group",
        //   eval_template_name: "TEST group",
        //   eval_required_keys: ["input", "output"],
        //   evalTemplateTags: ["FUTUREAGI_BUILT", "FUNCTION"],
        //   description:
        //     "Evaluations for analyzing marketing campaign effectiveness",
        //   isModelRequired: true,
        //   evalsActionType: "playground",
        //   isGroupEvals: true,
        // });
      }
      if (!isEvalsView) {
        if (activeButton === "create") {
          setVisibleSection("config");
          setCurrentTab("groups");
          setSelectedGroup(response?.id);
        } else {
          setVisibleSection("mapping");
          setSelectedEval({
            id: response?.id,
            name: response?.name,
            eval_template_name: response?.name,
            eval_required_keys: response?.required_keys ?? [],
            description: response?.description,
            isGroupEvals: true,
            evaluations: selectedEvals ?? [],
          });
        }
        if (groupId) {
          setSearchParam((prevSearchParams) => {
            prevSearchParams.delete("groupId");
            return prevSearchParams;
          });
        }
        setActionButtonConfig((prev) => ({
          ...prev,
          runLabel: "Add Group",
        }));
      } else {
        navigate("/dashboard/evaluations/groups");
        handleClose();
      }
      enqueueSnackbar("Evaluation group created successfully");
      reset({
        name: "",
        description: "",
      });
      setCreateGroupMode(false);
      setSelectedEvals([]);
      setActiveButton(null); // Reset active button
      queryClient.invalidateQueries({
        queryKey: ["eval-groups"],
      });
    },
    onError: () => {
      setActiveButton(null); // Reset active button on error
    },
  });

  const afterUpdateSuccess = () => {
    reset({
      name: "",
      description: "",
    });
    setCreateGroupMode(false);
    setSelectedEvals([]);
    setActiveButton(null);
    if (!isEvalsView) {
      if (activeButton === "create") {
        setVisibleSection("config");
        setCurrentTab("groups");
      } else {
        setVisibleSection("mapping");
        setSelectedEval({
          id: groupId,
          name: getValues("name"),
          eval_template_name: getValues("name"),
          description: getValues("description"),
          isGroupEvals: true,
          evaluations: selectedEvals ?? [],
        });
      }
      if (groupId) {
        setSearchParam((prevSearchParams) => {
          prevSearchParams.delete("groupId");
          return prevSearchParams;
        });
      }
    }

    queryClient.invalidateQueries({
      queryKey: ["eval-group", groupId],
    });
  };

  const { mutate: updateGroupMutation, isPending: isUpdatingGroup } =
    useMutation({
      mutationFn: async (data) => {
        return axios.patch(
          `${endpoints.develop.eval.groupEvals}${groupId}/`,
          data,
        );
      },

      onSuccess: (_, variables) => {
        handleUpdatingGroupList(variables);
      },
    });

  const { mutate: updateEvalList, isPending: isUpdatingGroupEvalList } =
    useMutation({
      mutationFn: async (payload) => {
        return axios.post(endpoints.develop.eval.editGroupEvalList, payload);
      },
      onSuccess: () => {
        afterUpdateSuccess();
      },
    });

  const handleUpdatingGroupList = () => {
    if (!groupId) return;
    const initialIds = new Set(
      initialGroupData?.current?.members?.map((e) => e.eval_template_id),
    );
    const currentIds = new Set(selectedEvals.map((e) => e.id));

    const added = selectedEvals
      .filter((e) => !initialIds.has(e.id))
      .map((e) => e.id);

    const deleted = initialGroupData?.current?.members
      ?.filter((e) => !currentIds.has(e.eval_template_id))
      .map((e) => e.eval_template_id);

    if (added.length === 0 && deleted.length === 0) {
      afterUpdateSuccess();
      return;
    }

    if (added?.length === 0 && deleted?.length === 0) {
      afterUpdateSuccess();
      return;
    }

    const payload = {
      eval_group_id: groupId,
      ...(added.length > 0 && { added_template_ids: added }),
      ...(deleted.length > 0 && { deleted_template_ids: deleted }),
    };
    updateEvalList(payload);
  };

  const removeEval = (id) => {
    setSelectedEvals((prev) => prev.filter((item) => item?.id !== id));
  };

  const handleCreateGroup = (data, test = false) => {
    const selectedEvalIds = selectedEvals?.map((evalItem) => evalItem?.id);
    const payload = {
      data: {
        ...data,
        eval_template_ids: selectedEvalIds,
      },
      test,
    };
    if (payload.data?.description === "") {
      delete payload.data.description;
    }
    mutate(payload);
  };

  // Handler for Create & Test button
  // const handleCreateAndTest = (data) => {
  //   setActiveButton("test");
  //   handleCreateGroup(data, true);
  // };

  // Handler for Create Group button
  const handleCreateGroupOnly = (data) => {
    setActiveButton("create");
    handleCreateGroup(data, false);
  };
  const handleCreateAndRUn = (data) => {
    setActiveButton("create-and-run");
    if (groupId) {
      updateGroupMutation(data);
    } else {
      handleCreateGroup(data, false);
    }
  };

  const required_keys = useMemo(() => {
    return getUniqueEvalRequiredKeys(selectedEvals);
  }, [selectedEvals]);

  const handleEditList = () => {
    if (isEvalsView) {
      setOpenCreateGroupDrawer(false);
    } else {
      setCurrentTab("evals");
      setVisibleSection("config");
      setCreateGroupMode(true);
    }
  };

  useEffect(() => {
    if (groupId && groupData) {
      initialGroupData.current = groupData?.result;
      const evalGroup =
        groupData?.result?.eval_group || groupData?.result?.evalGroup || {};
      reset({
        name: evalGroup?.name || "",
        description: evalGroup?.description || "",
      });
      setSelectedEvals(
        groupData?.result?.members?.map((evalItem) => ({
          id: evalItem?.eval_template_id,
          name: evalItem?.name,
          description: evalItem?.description,
        })) || [],
      );
    }
    return () => {
      initialGroupData.current = null;
      reset({
        name: "",
        description: "",
      });
    };
  }, [groupData, reset, groupId, setSelectedEvals]);

  const handleConfirmClose = () => {
    if (!isEvalsView) {
      setSelectedEvals([]);
      reset({
        name: "",
        description: "",
      });
      setSearchParam((prevSearchParams) => {
        prevSearchParams.delete("groupId");
        return prevSearchParams;
      });
    }
    handleClose();
  };

  const isFormLoading = isPending || isUpdatingGroup || isUpdatingGroupEvalList;

  return (
    <Drawer
      anchor="right"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          overflowY: "hidden",
          zIndex: 1,
          borderRadius: "0 !important",
          backgroundColor: "background.paper",
          width: "600px",
          padding: 2,
          display: "flex",
          flexDirection: "column",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
      open={open}
      onClose={handleConfirmClose}
    >
      <Stack
        direction={"column"}
        gap={2}
        sx={{
          height: "100%",
          overflow: "hidden",
        }}
      >
        <Stack
          direction={"row"}
          alignItems={"center"}
          justifyContent={"space-between"}
          sx={{ flexShrink: 0 }}
        >
          <Button
            disabled={isFormLoading}
            onClick={
              onBack
                ? () => {
                    setSearchParam((prevSearchParams) => {
                      prevSearchParams.delete("groupId");
                      return prevSearchParams;
                    });
                    reset({
                      name: "",
                      description: "",
                    });
                    onBack();
                  }
                : handleConfirmClose
            }
            size="small"
            variant="outlined"
            startIcon={
              <SvgColor
                sx={{
                  rotate: "180deg",
                  height: "16px",
                  width: "16px",
                  color: "text.primary",
                }}
                src="/assets/icons/custom/lucide--chevron-right.svg"
              />
            }
          >
            Back to Evaluations
          </Button>
          <IconButton disabled={isFormLoading} onClick={handleConfirmClose}>
            <Iconify color="text.primary" icon="mingcute:close-line" />
          </IconButton>
        </Stack>
        <Stack gap={0.25} sx={{ flexShrink: 0 }}>
          <Typography
            fontWeight={"fontWeightSemiBold"}
            typography={"m2"}
            color={"text.primary"}
          >
            {groupId ? "Update " : "Create "}
            Evaluation Group
          </Typography>
          <Typography
            fontWeight={"fontWeightRegular"}
            typography={"s1"}
            color={"text.primary"}
          >
            Create a new group with the selected evaluations
          </Typography>
        </Stack>
        <Stack
          sx={{
            flex: 1,
            minHeight: 0, // ✅ important for flexbox scrolling
            overflowY: "auto", // ✅ vertical scrolling
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <CardWrapper
            bgColor="background.neutral"
            textColor="text.disabled"
            order={0}
            title="Add Details"
            titleSx={{
              typography: "s2",
            }}
            sx={{ flexShrink: 0 }}
          >
            <Stack
              gap={2}
              sx={{
                padding: theme.spacing(2.25, 1.5),
              }}
            >
              <Stack gap={0.5}>
                <Typography
                  typography={"s2"}
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Group Information
                </Typography>
                <Typography
                  typography={"s2"}
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  Provide basic information about your evaluation group
                </Typography>
              </Stack>
              <FormTextFieldV2
                label="Name"
                control={control}
                fieldName="name"
                size="small"
                required
                fullWidth
                placeholder="Enter group name"
              />
              <FormTextFieldV2
                label="Description"
                control={control}
                fieldName="description"
                size="small"
                fullWidth
                multiline
                placeholder="Enter group description"
                rows={3}
              />
            </Stack>
          </CardWrapper>
          <CardWrapper
            bgColor="background.neutral"
            textColor="text.disabled"
            order={1}
            title="Review Group"
            titleSx={{
              typography: "s2",
            }}
            sx={{
              flexShrink: 0,
              mb: "4rem",
            }}
          >
            <Stack
              gap={2}
              sx={{
                padding: theme.spacing(2.25, 1.5),
                height: "100%",
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <Stack
                direction={"row"}
                alignItems={"center"}
                justifyContent={"space-between"}
                sx={{ flexShrink: 0 }}
              >
                <Typography
                  typography={"s2"}
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  Added evaluation ({selectedEvals?.length ?? 0})
                </Typography>
                <Button
                  disabled={isFormLoading}
                  onClick={handleEditList}
                  size="small"
                  color="primary"
                  variant="outlined"
                  startIcon={
                    <SvgColor
                      sx={{
                        height: "12px !important",
                        width: "12px !important",
                        color: "primary.main",
                      }}
                      src="/assets/icons/ic_pen.svg"
                    />
                  }
                >
                  Edit List
                </Button>
              </Stack>
              <ShowComponent condition={selectedEvals?.length > 0}>
                <Stack
                  direction={"row"}
                  gap={1}
                  alignItems={"center"}
                  sx={{
                    borderRadius: "2px",
                    border: "1px solid",
                    borderColor: "divider",
                    bgcolor: "background.neutral",
                    padding: theme.spacing(0.5),
                  }}
                >
                  <SvgColor
                    src={"/assets/icons/ic_info.svg"}
                    sx={{
                      height: "16px",
                      width: "16px",
                      color: "primary.main",
                    }}
                  />
                  <Typography
                    color={"text.primary"}
                    fontWeight={"fontWeightRegular"}
                    typography={"s3"}
                  >
                    Required variable: {required_keys.join(", ")}
                  </Typography>
                </Stack>
              </ShowComponent>
              <Stack
                sx={{
                  // overflowY: "auto",
                  flex: 1,
                  paddingRight: theme.spacing(0.5), // Add some padding for scrollbar
                }}
                gap={2}
              >
                {selectedEvals.map((evalItem) => (
                  <EvalCard
                    key={evalItem?.id}
                    name={evalItem?.name}
                    description={evalItem?.description}
                    onRemove={() => removeEval(evalItem?.id)}
                  />
                ))}
                {selectedEvals.length === 0 && (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      padding: theme.spacing(4),
                      color: "text.disabled",
                    }}
                  >
                    <Typography typography="s2">
                      No evaluations selected
                    </Typography>
                  </Box>
                )}
              </Stack>
            </Stack>
          </CardWrapper>
        </Stack>
        <Stack
          sx={{
            position: "absolute",
            bgcolor: "background.paper",
            left: 0,
            right: 0,
            bottom: 0,
            padding: theme.spacing(2),
            flexShrink: 0,
          }}
          direction={"row"}
          alignItems={"center"}
          gap={1.5}
          justifyContent={"flex-end"}
        >
          {/* <ShowComponent condition={isEvalsView}>
            <LoadingButton
              loading={isFormLoading && activeButton === "test"}
              type="button"
              disabled={
                !isValid || selectedEvals?.length === 0 || isFormLoading
              }
              variant="outlined"
              onClick={handleSubmit(handleCreateAndTest)}
              startIcon={
                !isFormLoading || activeButton !== "test" ? (
                  <SvgColor
                    sx={{
                      height: "20px",
                      width: "20px",
                      color: "text.primary",
                    }}
                    src={"/assets/icons/navbar/ic_get_started.svg"}
                  />
                ) : null
              }
            >
              Create & Test
            </LoadingButton>
          </ShowComponent> */}
          <LoadingButton
            loading={isFormLoading && activeButton === "create"}
            onClick={handleSubmit(handleCreateGroupOnly)}
            disabled={!isValid || selectedEvals?.length === 0 || isFormLoading}
            variant={isEvalsView ? "contained" : "outlined"}
            color={isEvalsView ? "primary" : undefined}
            startIcon={
              !isFormLoading || activeButton !== "create" ? (
                <SvgColor
                  src="/assets/icons/ic_add.svg"
                  sx={{ color: "inherit" }}
                />
              ) : null
            }
          >
            Create Group
          </LoadingButton>
          <ShowComponent condition={!isEvalsView}>
            <LoadingButton
              loading={isFormLoading && activeButton === "create-and-run"}
              onClick={handleSubmit(handleCreateAndRUn)}
              disabled={
                !isValid || selectedEvals?.length === 0 || isFormLoading
              }
              variant={"contained"}
              color={"primary"}
              startIcon={
                !isFormLoading || activeButton !== "create-and-run" ? (
                  <SvgColor
                    src="/assets/icons/ic_create-and-run.svg"
                    sx={{ color: "inherit" }}
                  />
                ) : null
              }
            >
              {groupId ? "Update Group & Run" : "Create Group & Run"}
            </LoadingButton>
          </ShowComponent>
        </Stack>
      </Stack>
    </Drawer>
  );
}

CreateEvaluationGroupDrawer.propTypes = {
  open: PropTypes.bool,
  handleClose: PropTypes.func,
  isEvalsView: PropTypes.bool,
  onBack: PropTypes.func,
};
