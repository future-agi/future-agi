import React, { lazy, Suspense, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Typography, IconButton, Button } from "@mui/material";
import { Icon } from "@iconify/react";
import UseCasesAndFilters from "./UseCasesAndFilters";
import { useEvalsList } from "./getEvalsList";
import { useDebounce } from "src/hooks/use-debounce";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { evalsFilterSchema, excludedEvals } from "./validation";
import { ShowComponent } from "../../../components/show";
import SkeletonEvaluationCardsGrid from "./SkeletonEvaluationCardsGrid";
import { useEvaluationContext } from "./context/EvaluationContext";
import { PropertyName, trackEvent } from "../../../utils/Mixpanel/mixpanel";
import { Events } from "../../../utils/Mixpanel/EventNames";
import GroupsGrid from "../../evals/Groups/GroupsGrid";
import { resetEvalStore, useEvalStore } from "../../evals/store/useEvalStore";
const EvaluationCardsGrid = lazy(() => import("./EvaluationCardsGrid"));
import IndividualGroup from "src/sections/evals/Groups/IndividualGroup";
import SvgColor from "src/components/svg-color";

const EvaluationsSelectionGrid = ({
  onClose,
  theme,
  datasetId,
  hideHeadings,
  order,
  onConfigBack,
  ...rest
}) => {
  const { control, watch, setValue } = useForm({
    defaultValues: {
      searchTerm: "",
      selectedUseCases: [],
      selectedEvalTags: [],
      selectedEvalCategory: "",
    },
    resolver: zodResolver(evalsFilterSchema),
  });
  const {
    setVisibleSection,
    currentTab,
    setCurrentTab,
    setSelectedGroup,
    selectedGroup,
  } = useEvaluationContext();

  const searchTerm = watch("searchTerm");
  const selectedEvalTags = watch("selectedEvalTags");
  const selectedEvalCategory = watch("selectedEvalCategory");
  const selectedUseCases = watch("selectedUseCases");
  const { createGroupMode } = useEvalStore();

  const debouncedSearchTerm = useDebounce(searchTerm.trim(), 300);
  const debouncedSelectedEvalTags = useDebounce(selectedEvalTags, 300);
  const debouncedSelectedUseCases = useDebounce(selectedUseCases, 300);

  const { data, isLoading } = useEvalsList(datasetId, {
    search_text: debouncedSearchTerm,
    eval_categories: selectedEvalCategory,
    eval_tags: debouncedSelectedEvalTags,
    use_cases: debouncedSelectedUseCases,
    ...(order !== undefined && { order }),
  });

  const evals = data?.evals.filter(
    (item) => !excludedEvals.includes(item.eval_template_name),
  );
  const recommendations = data?.eval_recommendations;

  const hasTracked = useRef(false);

  useEffect(() => {
    if (data && !isLoading && !hasTracked.current) {
      trackEvent(Events.evalsPageLoaded, {
        [PropertyName.status]: "loaded",
      });
      hasTracked.current = true;
    }
  }, [isLoading, data]);

  return (
    <Box
      sx={{
        width: rest?.isEvalsView
          ? "100%"
          : {
              xs: "95vw", // Extra small screens
              sm: "90vw", // Small screens
              md: "85vw", // Medium screens
              lg: "1220px", // Large screens and up
            },
        height: "100%",
        overflowY: "hidden",
      }}
    >
      <Box
        display={hideHeadings ? "none" : "flex"}
        flexDirection="column"
        width="100%"
        gap={theme.spacing(1)}
        mb={theme.spacing(1)}
      >
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
            <IconButton
              onClick={() => {
                setVisibleSection("list");
                resetEvalStore();
                setCurrentTab("evals");
                setSelectedGroup(null);
                if (onConfigBack) {
                  onConfigBack();
                }
              }}
              size="small"
              sx={{
                border: "1px solid",
                borderColor: theme.palette.divider,
                p: theme.spacing(0.2),
              }}
            >
              <Icon icon="solar:arrow-left-linear" />
            </IconButton>
            <Typography fontSize={16} fontWeight={600}>
              Evaluations
            </Typography>
          </Box>

          <IconButton
            onClick={() => {
              onClose();
              resetEvalStore();
              setCurrentTab("evals");
              setSelectedGroup(null);
            }}
            sx={{ p: 0 }}
          >
            <Icon icon="solar:close-linear" />
          </IconButton>
        </Box>

        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography
            color={theme.palette.text.primary}
            fontSize={13}
            fontWeight={400}
          >
            Choose from our evaluations that best match your goals, or you can
            define your own to suit your specific use case.
          </Typography>
          <Button
            variant="outlined"
            size="small"
            sx={{
              color: "text.primary",
              borderColor: "text.disabled",
            }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href="https://docs.futureagi.com/docs/evaluation"
            target="_blank"
          >
            View Docs
          </Button>
        </Box>
      </Box>
      <Box
        sx={{
          height: rest.isEvalsView ? "calc(100% - 10px)" : "calc(100% - 60px)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box sx={{ flexShrink: 0 }}>
          <UseCasesAndFilters
            currentTab={currentTab}
            setCurrentTab={setCurrentTab}
            isEvalsView={rest?.isEvalsView}
            control={control}
            setValue={setValue}
          />
        </Box>
        <ShowComponent condition={currentTab === "evals"}>
          <Typography
            sx={{
              fontSize: "12px",
              fontWeight: 500,
              lineHeight: "18px",
              pt: 3,
              pb: 2,
            }}
          >
            {!isLoading
              ? `All (${evals?.length ?? 0})`
              : "Loading Evaluations..."}
          </Typography>
        </ShowComponent>
        <Box
          sx={{
            flexGrow: 1,
            mb: 1,
            overflowY: "auto",
            scrollbarWidth: "none",
            msOverflowStyle: "none",
            "&::-webkit-scrollbar": {
              display: "none",
            },
            ...(createGroupMode && { pb: "1rem" }),
          }}
        >
          <ShowComponent condition={isLoading && currentTab === "evals"}>
            <SkeletonEvaluationCardsGrid />
          </ShowComponent>

          <ShowComponent condition={!isLoading && currentTab === "evals"}>
            <Suspense fallback={<SkeletonEvaluationCardsGrid />}>
              <EvaluationCardsGrid
                evals={evals}
                recommendations={recommendations}
                control={control}
                setValue={setValue}
                debouncedSearchTerm={searchTerm}
                {...rest}
              />
            </Suspense>
          </ShowComponent>
          <ShowComponent condition={currentTab === "groups"}>
            <Box
              sx={{
                py: 2.5,
              }}
            >
              <ShowComponent condition={!selectedGroup}>
                <GroupsGrid
                  isEvalsView={false}
                  onGroupSelect={(id) => setSelectedGroup(id)}
                />
              </ShowComponent>
              <ShowComponent condition={selectedGroup}>
                <IndividualGroup
                  groupId={selectedGroup}
                  onReset={() => setSelectedGroup(null)}
                />
              </ShowComponent>
            </Box>
          </ShowComponent>
        </Box>
      </Box>
    </Box>
  );
};

EvaluationsSelectionGrid.propTypes = {
  onClose: PropTypes.func,
  theme: PropTypes.object,
  datasetId: PropTypes.string,
  hideHeadings: PropTypes.bool,
  order: PropTypes.string,
  onConfigBack: PropTypes.func,
};

export default EvaluationsSelectionGrid;
