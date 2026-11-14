import { LoadingButton } from "@mui/lab";
import { Box, Collapse, Divider, IconButton, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useEffect, useMemo } from "react";
import Iconify from "src/components/iconify";
import PlaygroundInput from "src/components/PlaygroundInput/PlaygroundInput";
import { ShowComponent } from "src/components/show";
import EvalsOutput from "src/sections/evals/EvalDetails/EvalsConfig/EvalsOutput";
import axios, { endpoints } from "src/utils/axios";
import { extractVariables } from "src/utils/utils";

const CustomEvalsPlayground = ({ open, onClose, formData }) => {
  const [value, setValue] = React.useState([]);
  const [results, setResults] = React.useState(null);

  const extractedKey = useMemo(() => {
    // For Function evals, use requiredKeys from the function template config
    // For other evals, extract variables from criteria
    if (formData?.template_type === "Function") {
      // Get requiredKeys from the selected function eval template
      // requiredKeys may be an array of strings or objects with {text: "key"}
      const keys = formData?.required_keys || ["text"];
      return keys.map((key) => (typeof key === "object" ? key.text : key));
    }
    return extractVariables(formData?.criteria || "");
  }, [formData?.criteria, formData?.template_type, formData?.required_keys]);

  const { variables, variableConfig } = useMemo(() => {
    const allVariable = extractedKey || [];
    const allVariableConfig = formData?.input_data_types || {};
    setValue(
      allVariable?.map((_item) => ({ type: "text", value: "", url: "" })),
    );
    if (!formData?.input_data_types) {
      allVariable?.forEach((item) => {
        allVariableConfig[item] = "text";
      });
    }
    return { variables: allVariable, variableConfig: allVariableConfig };
  }, [formData]);

  const getInputs = (data, requiredInput) => {
    const inputDataTypes = {};
    const mapping = {};
    data.forEach((item, index) => {
      if (item.type === "text") {
        inputDataTypes[requiredInput[index]] = item.type;
        mapping[requiredInput[index]] = item.value;
      } else {
        inputDataTypes[requiredInput[index]] = item.type;
        mapping[requiredInput[index]] = item.url;
      }
    });
    return { inputDataTypes, mapping };
  };

  const transformCriteria = () => {
    let criteriaCopy = formData.criteria;
    extractedKey.forEach((key, index) => {
      const regex = new RegExp(`\\{\\{${key}\\}\\}`, "g");
      criteriaCopy = criteriaCopy.replace(regex, `{{variable_${index + 1}}}`);
    });
    return criteriaCopy;
  };

  const transformChoices = (choices) => {
    const choicesCopy = {};
    choices?.forEach((choice) => {
      choicesCopy[choice.key] = choice.value;
    });
    return choicesCopy;
  };

  const handleEvaluate = () => {
    const { inputDataTypes, mapping } = getInputs(value, variables);

    const transformedCritera =
      formData.templateType === "Futureagi"
        ? transformCriteria()
        : formData.criteria;

    const payload = {
      name: formData.name,
      reason_column: false,
      eval_tags: formData.tags.map((tag) => tag.value),
      description: formData.description,
      criteria: transformedCritera,
      model: formData.config.model,
      required_keys: extractedKey,
      optional_keys: [],
      variable_keys: variables,
      run_prompt_column: false,
      template_name: formData.template_name,
      mapping: mapping,
      config: {
        ...formData.config,
        model: formData.config.model,
        mapping: mapping,
        reason_column: false,
      },
      output: formData.output_type,
      config_params_desc: {},
      config_params_option: {},
      choices: transformChoices(formData.choices),
      check_internet: formData.checkInternet,
      template_type: formData.templateType,
      output_type: formData.output_type,
      multi_choice: formData.multiChoice,
      input_data_types: inputDataTypes,
      eval_type_id: formData.evalTypeId,
    };

    evaluateMutate(payload);
  };

  const disableEvaluate = useMemo(() => {
    let disabled = false;
    value.forEach((item) => {
      if (
        (item.type === "text" && !item.value) ||
        (item.type !== "text" && !item.url)
      ) {
        disabled = true;
      }
    });
    return disabled;
  }, [value]);

  const handleOnChage = (e, index) => {
    const { value, url, type } = e;
    setValue((prev) => {
      const newValue = [...prev];
      newValue[index] = {
        type,
        ...(type === "text" && { value: value, url: "" }),
        ...(type !== "text" && { url: url, value: "" }),
      };
      return newValue;
    });
  };

  const {
    mutate: evaluateMutate,
    isPending: isEvaluating,
    reset,
  } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.eval.testEvaluation, data),
    onSuccess: (data) => {
      const { result } = data?.data || {};
      setResults({ output: result.output, reason: result.reason });
    },
  });

  useEffect(() => {
    setValue([]);
    setResults(null);
    reset();
  }, [open]);

  return (
    <Collapse
      in={open}
      orientation="vertical"
      sx={{ display: open ? "block" : "none", height: "100%", flex: 1 }}
    >
      <ShowComponent condition={open}>
        <Box
          sx={{
            display: "flex",
            gap: 2,
            width: "500px",
            borderRight: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box
            sx={{
              height: "calc(100vh - 30px)",
              width: "100%",
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              overflowY: "auto",
            }}
          >
            <Box
              sx={{
                display: "flex",
                gap: "4px",
                justifyContent: "space-between",
              }}
            >
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "4px" }}
              >
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Playground
                </Typography>
                <Typography
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  Test your evaluations on our playground
                </Typography>
              </Box>
              <IconButton onClick={onClose}>
                <Iconify
                  icon="solar:close-linear"
                  sx={{
                    width: (theme) => theme.spacing(2),
                    height: (theme) => theme.spacing(2),
                    color: "text.primary",
                  }}
                />
              </IconButton>
            </Box>
            {variables?.map((item, index) => {
              return (
                <PlaygroundInput
                  key={index}
                  showTabs={true}
                  fieldTitle={item}
                  onChange={(e) => handleOnChage(e, index)}
                  inputType={variableConfig[item] || "text"}
                  value={value[index]}
                  required={true}
                  hideAudio={formData.config.model !== "turing_large"}
                />
              );
            })}
            <Box display={"flex"} justifyContent={"flex-end"}>
              <LoadingButton
                variant="contained"
                color="primary"
                size="small"
                type="button"
                onClick={handleEvaluate}
                loading={isEvaluating}
                disabled={disableEvaluate}
              >
                Evaluate
              </LoadingButton>
            </Box>
            <Divider orientation="horizontal" />
            <EvalsOutput results={results} />
          </Box>
          <Divider orientation="vertical" />
        </Box>
      </ShowComponent>
    </Collapse>
  );
};

export default CustomEvalsPlayground;

CustomEvalsPlayground.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  formData: PropTypes.object,
};
