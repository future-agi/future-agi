import React, { useEffect, useRef } from "react";
import { Box, Button, Grid, Stack, Typography, useTheme } from "@mui/material";
import Section from "src/components/section";
import EvaluationCard from "./EvaluationCard";
import PropTypes from "prop-types";
import { useEvaluationContext } from "./context/EvaluationContext";
import EvalPlayground from "src/sections/evals/EvalPlayground/EvalPlayground";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import EditCustomEvals from "./EditCustomEvals";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { resetEvalStore, useEvalStore } from "../../evals/store/useEvalStore";
import SvgColor from "../../../components/svg-color";
import { ShowComponent } from "../../../components/show";
import CreateEvaluationGroupDrawer from "./CreateEvaluationGroupDrawer";
import { useLocation, useNavigate } from "react-router";
import axios, { endpoints } from "../../../utils/axios";
import { LoadingButton } from "@mui/lab";
import { useSearchParams } from "react-router-dom";

const SpecialEvaluationCard = () => {
  const { setVisibleSection } = useEvaluationContext();
  const { role } = useAuthContext();
  const theme = useTheme();

  const gradientColor =
    theme.palette.mode === "light"
      ? "linear-gradient(135deg, var(--primary-main), #CF6BE8)"
      : "linear-gradient(135deg, #FFFFFF, #E6E6E7)";

  return (
    <Box
      sx={{
        position: "relative",
        minHeight: "114px",
        height: "100%",
        borderRadius: "4px",
        cursor: RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][role]
          ? "pointer"
          : "default",
        "&::before": {
          content: '""',
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          borderRadius: "4px",
          padding: "2px",
          background: gradientColor,
          WebkitMask:
            "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
          WebkitMaskComposite: "xor",
          maskComposite: "exclude",
          pointerEvents: "none",
        },
      }}
      onClick={() => {
        if (RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS][role]) {
          setVisibleSection("custom");
        }
      }}
    >
      <Section
        variant="panel"
        sx={{
          height: "100%",
          width: "100%",
          display: "flex",
          flexDirection: "column",
          px: "16px",
          py: "12px",
        }}
      >
        <Box sx={{ display: "flex", gap: "2px", flexDirection: "column" }}>
          <Typography
            sx={{
              fontWeight: 500,
              fontSize: "13px",
              lineHeight: "22px",
              color: "text.primary",
            }}
          >
            Create your own evals
          </Typography>
          <Typography
            sx={{
              fontWeight: 400,
              fontSize: "12px",
              lineHeight: "18px",
              color: "text.disabled",
            }}
          >
            Configure your own evaluation to suit your specific use case.
          </Typography>
        </Box>
      </Section>
    </Box>
  );
};

const EvaluationCardsGrid = ({
  evals,
  recommendations,
  control,
  setValue,
  showRecommendations = true,
  debouncedSearchTerm,
  ...rest
}) => {
  const queryClient = useQueryClient();
  const theme = useTheme();
  const location = useLocation();
  const {
    playgroundEvaluation,
    setPlaygroundEvaluation,
    setVisibleSection,
    setCurrentTab,
    setSelectedGroup,
  } = useEvaluationContext();
  const {
    selectedEvals,
    createGroupMode,
    setCreateGroupMode,
    setSelectedEvals,
    openCreateEvalGroupDrawer,
    setOpenCreateGroupDrawer,
    EditGroupMode,
    setEditGroupMode,
  } = useEvalStore();
  const [searchParams] = useSearchParams();
  const groupId = searchParams.get("group-id") ?? null;
  const editGroupId = searchParams.get("groupId");
  const navigate = useNavigate();
  const [_, setSearchParams] = useSearchParams();

  const handleNavigateToGroup = () => {
    if (rest?.isEvalsView) {
      navigate(`/dashboard/evaluations/groups/${groupId}`);
    } else {
      setCurrentTab("groups");
      setSelectedGroup(groupId);
      setSearchParams((prevSearchParams) => {
        prevSearchParams.delete("group-id");
        return prevSearchParams;
      });
    }
  };

  const { mutate: updateEvalList, isPending: isUpdatingGroupEvalList } =
    useMutation({
      mutationFn: async (payload) => {
        return axios.post(endpoints.develop.eval.editGroupEvalList, payload);
      },
      onSuccess: () => {
        resetEvalStore();
        handleNavigateToGroup();
      },
    });

  const initialSelectedEvals = useRef(selectedEvals);

  const sortedEvals = [...(evals || [])].sort((a, b) => {
    const aIsRecommended = recommendations.includes(a.name);
    const bIsRecommended = recommendations.includes(b.name);
    return aIsRecommended === bIsRecommended ? 0 : aIsRecommended ? -1 : 1;
  });

  const handleUpdatingGroupList = () => {
    if (!groupId) return;
    const initialIds = new Set(initialSelectedEvals.current.map((e) => e.id));
    const currentIds = new Set(selectedEvals.map((e) => e.id));

    const added = selectedEvals
      .filter((e) => !initialIds.has(e.id))
      .map((e) => e.id);

    const deleted = initialSelectedEvals.current
      .filter((e) => !currentIds.has(e.id))
      .map((e) => e.id);

    if (added.length === 0 && deleted.length === 0) {
      handleNavigateToGroup();
      return;
    }

    const payload = {
      eval_group_id: groupId,
      ...(added.length > 0 && { added_template_ids: added }),
      ...(deleted.length > 0 && { deleted_template_ids: deleted }),
    };
    updateEvalList(payload);
  };

  const onClosePlayground = () => {
    queryClient.invalidateQueries({
      queryKey: ["user-eval-list"],
    });
    setPlaygroundEvaluation(null);
  };

  const handleCancelClick = () => {
    setCreateGroupMode(false);
    setSelectedEvals([]);
    if (groupId && EditGroupMode) {
      handleNavigateToGroup();
    }
  };

  useEffect(() => {
    if (location?.state?.createMode) {
      setCreateGroupMode(true);
    }
  }, [
    location?.state?.createMode,
    location?.state?.editMode,
    location?.state?.selectedEvals,
    setCreateGroupMode,
    setEditGroupMode,
    setSelectedEvals,
  ]);

  const handleCreateGroup = () => {
    if (rest?.isEvalsView) {
      setOpenCreateGroupDrawer(true);
    } else {
      setVisibleSection("create-group");
    }
  };

  const baseEvals = showRecommendations ? sortedEvals : evals;

  // IDs of initial selected evals
  const initialSelectedIds = new Set(
    initialSelectedEvals.current?.map((sel) => sel?.id) || [],
  );

  // Filter out initial selected to avoid duplicates
  const filteredBaseEvals =
    baseEvals?.filter((item) => !initialSelectedIds.has(item?.id)) || [];

  let displayedEvals;

  if (debouncedSearchTerm) {
    const lowerSearch = debouncedSearchTerm.toLowerCase();

    // Items matching search from baseEvals
    const searchedItems = filteredBaseEvals;
    // Initial selected items that also match search: show first, preserve API sort
    const selectedMatchingSearch =
      initialSelectedEvals.current?.filter((sel) =>
        sel.name.toLowerCase().includes(lowerSearch),
      ) || [];

    // Initial selected items not matching search: sort alphabetically
    const selectedNotMatchingSearch =
      initialSelectedEvals.current
        ?.filter((sel) => !sel.name.toLowerCase().includes(lowerSearch))
        .sort((a, b) => a.name.localeCompare(b.name)) || [];

    // Combine: selected matching first, then API search results, then non-matching selected
    const existingIds = new Set(
      [
        ...selectedMatchingSearch,
        ...searchedItems,
        ...selectedNotMatchingSearch,
      ].map((e) => e?.id),
    );

    displayedEvals = [
      ...selectedMatchingSearch,
      ...searchedItems,
      ...selectedNotMatchingSearch,
      ...selectedEvals.filter((e) => !existingIds.has(e?.id)),
    ];
  } else {
    // No search: initial selected at front, rest follow
    displayedEvals = [
      ...(initialSelectedEvals.current || []),
      ...filteredBaseEvals,
    ];
  }

  return (
    <Box sx={{ flexGrow: 1 }}>
      <Grid
        container
        spacing={2}
        sx={{
          mb: "2rem",
        }}
      >
        {/* Special Card as the first item */}
        <Grid item xs={12} sm={6} md={4}>
          <SpecialEvaluationCard />
        </Grid>

        {displayedEvals?.map((evalItem, index) => {
          const isRecommended = recommendations?.includes(evalItem.name);
          return (
            <Grid item xs={12} sm={6} md={4} key={evalItem.id || index}>
              <EvaluationCard
                selectedEvals={selectedEvals}
                createGroupMode={createGroupMode}
                setSelectedEvals={setSelectedEvals}
                eval={evalItem}
                recommended={isRecommended}
                eval_category={evalItem.type}
                control={control}
                setValue={setValue}
                {...rest}
              />
            </Grid>
          );
        })}
      </Grid>
      <EvalPlayground
        open={playgroundEvaluation?.evalsActionType === "playground"}
        onClose={onClosePlayground}
        evaluation={playgroundEvaluation}
      />
      <EditCustomEvals
        open={playgroundEvaluation?.evalsActionType === "edit"}
        onClose={onClosePlayground}
        evaluation={playgroundEvaluation}
      />
      <CreateEvaluationGroupDrawer
        open={openCreateEvalGroupDrawer}
        handleClose={() => setOpenCreateGroupDrawer(false)}
        isEvalsView={rest?.isEvalsView}
      />
      <ShowComponent condition={createGroupMode}>
        <Stack
          sx={{
            padding: theme.spacing(2, 4),
            bgcolor: "background.paper",
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            width: "100%",
          }}
          direction={"row"}
          alignItems={"center"}
          justifyContent={"flex-end"}
          gap={2}
        >
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            Evals Selected ({selectedEvals?.length ?? 0})
          </Typography>
          <Button
            disabled={isUpdatingGroupEvalList}
            onClick={handleCancelClick}
            sx={{ minWidth: "162px" }}
            variant="outlined"
          >
            Cancel
          </Button>
          {EditGroupMode ? (
            <LoadingButton
              loading={isUpdatingGroupEvalList}
              color="primary"
              variant="contained"
              onClick={handleUpdatingGroupList}
            >
              Add Evaluations
            </LoadingButton>
          ) : (
            <Button
              disabled={!selectedEvals || selectedEvals?.length === 0}
              onClick={handleCreateGroup}
              sx={{ minWidth: "162px" }}
              color="primary"
              variant="outlined"
              startIcon={
                <SvgColor
                  src={"/assets/icons/ic_add.svg"}
                  sx={{ color: "inherit" }}
                />
              }
            >
              {editGroupId ? "Update List" : "Create Group"}
            </Button>
          )}
        </Stack>
      </ShowComponent>
    </Box>
  );
};

EvaluationCardsGrid.propTypes = {
  evals: PropTypes.array,
  recommendations: PropTypes.array,
  control: PropTypes.object,
  setValue: PropTypes.func,
  showRecommendations: PropTypes.bool,
  debouncedSearchTerm: PropTypes.string,
};

export default EvaluationCardsGrid;
