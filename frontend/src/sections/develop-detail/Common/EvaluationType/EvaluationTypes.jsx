import {
  Box,
  Button,
  Skeleton,
  Tab,
  Tabs,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import Label from "src/components/label";
import EvaluationTypeCard from "../EvaluationTypeCard";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import EvalTypesSkeleton from "./EvalTypesSkeleton";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "src/components/FormSearchField/FormSearchField";

const categories = [
  { label: "Conversation", value: "CONVERSATION" },
  { label: "Image", value: "IMAGE" },
  { label: "Future Evals", value: "FUTURE_EVALS" },
  { label: "LLMs", value: "LLMS" },
  { label: "Custom", value: "CUSTOM" },
  { label: "Function", value: "FUNCTION" },
  { label: "Rag", value: "RAG" },
  { label: "Safety", value: "SAFETY" },
  { label: "Hallucination", value: "HALLUCINATION" },
  { label: "Text", value: "TEXT" },
  { label: "Audio", value: "AUDIO" },
];

const evalTemplateTags = (evalItem) =>
  Array.isArray(evalItem?.eval_template_tags)
    ? evalItem.eval_template_tags
    : [];

const EvaluationTypes = ({
  onClose,
  onOptionClick,
  datasetId,
  isNarrow = false,
}) => {
  const theme = useTheme();
  const [searchText, setSearchText] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");

  const searchDebounce = useDebounce(searchText.trim(), 300);

  const { data: evalList, isLoading: loadingEvalList } = useQuery({
    queryKey: ["develop", "preset-eval-list"],
    queryFn: () =>
      axios.get(endpoints.develop.eval.getEvalsList(datasetId), {
        params: {
          eval_type: "preset",
          search_text: searchDebounce || "",
        },
      }),
    select: (d) => d?.data?.result?.evals,
  });
  const evals = useMemo(
    () => (Array.isArray(evalList) ? evalList : []),
    [evalList],
  );

  const filteredEvalList = useMemo(() => {
    const lowerCaseSearchText = (searchDebounce || "").toLowerCase();

    return evals.filter((each) => {
      let matchesCategory = true;

      if (selectedCategory !== "all") {
        matchesCategory = evalTemplateTags(each).includes(selectedCategory);
      }

      const matchesSearch =
        !lowerCaseSearchText ||
        each.name.toLowerCase().includes(lowerCaseSearchText);

      return matchesCategory && matchesSearch;
    });
  }, [selectedCategory, evals, searchDebounce]);

  const categoryCount = useMemo(() => {
    return evals
      .filter(
        (each) =>
          !searchDebounce ||
          each.name.toLowerCase().includes(searchDebounce.toLowerCase()),
      )
      .reduce(
        (acc, each) => {
          acc["all"] = (acc["all"] || 0) + 1;
          evalTemplateTags(each).forEach((tag) => {
            acc[tag] = (acc[tag] || 0) + 1;
          });
          return acc;
        },
        { all: 0 },
      );
  }, [evals, searchDebounce]);

  const handleSearchChange = (event) => {
    const value = event.target.value;
    setSearchText(value);
    if (!value) setSelectedCategory("all");
  };
  return (
    <Box
      sx={{
        width: isNarrow ? "51vw" : "60vw",
        height: "100vh",
        borderRight: "1px solid",
        borderColor: "divider",
        overflowY: "auto",
      }}
    >
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            padding: "20px",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Typography fontWeight={700} color="text.primary">
            Preset Evaluations
          </Typography>
          <Button variant="soft" size="small" onClick={onClose}>
            Close
          </Button>
        </Box>

        <Box sx={{ px: "20px", pb: 2 }}>
          <FormSearchField
            fullWidth
            size="small"
            placeholder="Search"
            searchQuery={searchText}
            onChange={handleSearchChange}
          />
        </Box>

        <Box
          sx={{
            paddingX: "20px",
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Tabs
            textColor="primary"
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
              },
            }}
            value={selectedCategory}
            onChange={(e, value) => setSelectedCategory(value)}
            TabScrollButtonProps={{
              sx: {
                width: "28px !important",
                height: "28px",
                alignSelf: "center",
              },
            }}
          >
            <Tab
              label="All"
              value="all"
              sx={{
                "&.MuiTab-root": {
                  marginRight: 2,
                  fontSize: 12,
                },
              }}
              icon={
                loadingEvalList ? (
                  <Skeleton variant="text" width={20} height={35} />
                ) : (
                  <Label variant="soft" color="success" sx={{ fontSize: 12 }}>
                    {/* {evalList.length} */} {categoryCount["all"]}
                  </Label>
                )
              }
              iconPosition="end"
            />
            {categories.map(({ label, value }) => (
              <Tab
                key={value}
                label={label}
                value={value}
                icon={
                  loadingEvalList ? (
                    <Skeleton variant="text" width={20} height={35} />
                  ) : (
                    <Label variant="soft" color="success" sx={{ fontSize: 12 }}>
                      {categoryCount[value] ?? 0}
                    </Label>
                  )
                }
                iconPosition="end"
                sx={{
                  "&.MuiTab-root": {
                    marginRight: 2,
                    fontSize: 12,
                    "&.Mui-selected": {
                      color: "inherit",
                    },
                    color: "text.secondary",
                  },
                }}
              />
            ))}
          </Tabs>
        </Box>
      </Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: "8px",
          padding: "20px",
        }}
      >
        {loadingEvalList && <EvalTypesSkeleton />}
        {filteredEvalList.map((eachEval) => {
          const { name, id, description } = eachEval;

          return (
            <EvaluationTypeCard
              key={id}
              title={name}
              subTitle={description}
              tags={evalTemplateTags(eachEval)}
              onClick={() => onOptionClick(eachEval)}
            />
          );
        })}
      </Box>
    </Box>
  );
};

EvaluationTypes.propTypes = {
  onClose: PropTypes.func,
  onOptionClick: PropTypes.func,
  datasetId: PropTypes.string,
  isNarrow: PropTypes.bool,
};

export default EvaluationTypes;
