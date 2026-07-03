import { Box, Skeleton, Tab, Tabs, Typography, useTheme } from "@mui/material";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import Label from "src/components/label";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import EvalTypesSkeleton from "src/sections/develop-detail/Common/EvaluationType/EvalTypesSkeleton";
import EvaluationTypeCard from "src/sections/develop-detail/Common/EvaluationTypeCard";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { normalizeEvalSearchText } from "../utils/search";

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
];

const CustomEvalDrawer = ({ onClose, onOptionClick }) => {
  const theme = useTheme();
  const [searchText, setSearchText] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const normalizedSearchText = useMemo(
    () => normalizeEvalSearchText(searchText),
    [searchText],
  );

  const {
    data: evalList,
    isLoading: loadingEvalList,
    isFetching,
  } = useQuery({
    queryKey: ["develop", "preset-eval-list", normalizedSearchText],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getEvalsList(
          "2063cf96-40fc-4840-b5cd-ce48f06c24ea",
        ),
        {
          params: {
            eval_type: "preset",
            search_text: normalizedSearchText || "",
          },
        },
      ),
    select: (d) => d?.data?.result?.evals,
  });

  const filteredEvalList = useMemo(() => {
    if (!evalList) return [];
    if (selectedCategory === "all") {
      return evalList.filter(
        (each) =>
          !normalizedSearchText ||
          each.name.toLowerCase().includes(normalizedSearchText),
      );
    }
    return evalList.filter(
      (each) =>
        each.eval_template_tags.includes(selectedCategory) &&
        (!normalizedSearchText ||
          each.name.toLowerCase().includes(normalizedSearchText)),
    );
  }, [selectedCategory, evalList, normalizedSearchText]);

  const categoryCount = useMemo(() => {
    if (!evalList) return {};
    return evalList
      .filter(
        (each) =>
          !normalizedSearchText ||
          each.name.toLowerCase().includes(normalizedSearchText),
      )
      .reduce(
        (acc, each) => {
          acc["all"] = (acc["all"] || 0) + 1;
          each.eval_template_tags.forEach(
            (tag) => (acc[tag] = (acc[tag] || 0) + 1),
          );
          return acc;
        },
        { all: 0 },
      );
  }, [evalList, normalizedSearchText]);

  const handleSearchChange = (event) => {
    const value = event.target.value;
    setSearchText(value);
    if (!value) setSelectedCategory("all");
  };
  return (
    <Box
      sx={{
        width: "100%",
        height: "100vh",
        borderRight: "1px solid",
        borderColor: "divider",
        background: "background.paper",
        overflowY: "auto",
        padding: 2,
      }}
    >
      <Box
        sx={{
          position: "relative",
          top: 0,
          zIndex: 10,
          backgroundColor: "background.paper",
        }}
      >
        <Box
          sx={{
            paddingBottom: "20px",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Typography fontWeight={600} fontSize={"12px"} color="text.disabled">
            Create from a custom eval
          </Typography>
          <Iconify
            icon="flowbite:x-outline"
            sx={{ cursor: "pointer" }}
            onClick={onClose}
          />
        </Box>

        <Box sx={{ px: 1, pb: 2 }}>
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
            //   paddingX: "20px",
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Tabs
            TabIndicatorProps={{
              style: {
                backgroundColor: theme.palette.primary.main,
              },
            }}
            value={selectedCategory}
            onChange={(e, value) => setSelectedCategory(value)}
            sx={{
              minHeight: 0,
              "& .MuiTab-root": {
                margin: "0 !important",
                fontWeight: "600",
                color: "primary.main",
                "&:not(.Mui-selected)": {
                  color: "text.disabled",
                  fontWeight: "500",
                },
              },
              ml: -3,
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
                px: theme.spacing(1),
              }}
              icon={
                loadingEvalList ? (
                  <Skeleton variant="text" width={20} height={35} />
                ) : (
                  <Label
                    variant="soft"
                    color={selectedCategory === "all" ? "success" : "default"}
                    sx={{
                      fontSize: 12,
                      fontWeight: selectedCategory === "all" ? 600 : 500,
                    }}
                  >
                    {categoryCount["all"]}
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
                    <Label
                      variant="soft"
                      color={selectedCategory === value ? "success" : "default"}
                      sx={{
                        fontSize: 12,
                        fontWeight: selectedCategory === value ? 600 : 500,
                      }}
                    >
                      {categoryCount[value] ?? 0}
                    </Label>
                  )
                }
                iconPosition="end"
                sx={{
                  "&.MuiTab-root": {
                    marginRight: 2,
                    fontSize: 12,
                    px: theme.spacing(1),
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
        {loadingEvalList || isFetching ? (
          <EvalTypesSkeleton />
        ) : (
          filteredEvalList.map((eachEval) => {
            const {
              name,
              id,
              description,
              eval_template_tags: evalTemplateTags,
            } = eachEval;

            return (
              <EvaluationTypeCard
                key={id}
                title={name}
                subTitle={description}
                tags={evalTemplateTags}
                onClick={() => onOptionClick(eachEval)}
              />
            );
          })
        )}
      </Box>
    </Box>
  );
};

CustomEvalDrawer.propTypes = {
  onClose: PropTypes.func,
  onOptionClick: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default CustomEvalDrawer;
