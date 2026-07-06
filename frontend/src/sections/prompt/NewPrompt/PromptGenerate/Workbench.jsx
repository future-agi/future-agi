import React, { useEffect, useState } from "react";
import { Box, Button, CircularProgress, Typography } from "@mui/material";
import { useFieldArray } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import Iconify from "src/components/iconify";
import { trackEvent, Events } from "src/utils/Mixpanel";
import "react-quill/dist/quill.snow.css"; // import styles
import PropTypes from "prop-types";

import InputPromptBoxV3 from "../../PromptBox/InputPromptBoxV3";
// import PrintFormValues from "src/utils/PrintFormValues";

const Workbench = ({
  control,
  loading,
  handleLabelsAdd,
  appliedVariableData,
}) => {
  const [, setFieldImages] = useState([]);

  const { fields, append, remove } = useFieldArray({
    control,
    name: `config.messages`,
  });

  useEffect(() => {
    const images = fields.map((field, index) => {
      const imagesPresent = field.content.length > 1;
      return {
        id: index,
        images: imagesPresent ? field?.content.slice(1) : [],
      };
    });
    setFieldImages(images);
  }, [fields]);

  const handleDuplicate = (content, role) => {
    append({
      id: getRandomId(),
      role: role,
      content,
    });
  };

  return (
    <Box
      sx={{
        width: "100%",
        padding: "0 17px 17px 17px",
        borderRight: 3,
        borderRightStyle: "solid",
        borderRightColor: "divider",
        display: "flex",
        flexDirection: "column",
        overflowY: "auto",
      }}
    >
      {/* <PrintFormValues control={control} logName="Workbench" /> */}
      <Typography
        variant="subtitle2"
        color="text.primary"
        sx={{ margin: "17px 0 15px 0" }}
      >
        WORKBENCH
      </Typography>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          alignItems: "center",
          mb: 3,
        }}
      >
        <Typography fontSize="12px" color="text.secondary">
          Test, Evaluate, and refine your prompts before running them on the
          full dataset, ensuring optimal results
        </Typography>
      </Box>

      {loading ? (
        <Box
          sx={{
            alignItems: "center",
            display: "flex",
            flex: 1,
            justifyContent: "center",
          }}
        >
          <CircularProgress />
        </Box>
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {fields?.length > 0 &&
            fields?.map((m, idx) => {
              return (
                <InputPromptBoxV3
                  key={m.id}
                  onDuplicate={handleDuplicate}
                  onRemove={idx ? () => remove(idx) : undefined}
                  control={control}
                  roleFieldName={`config.messages.${idx}.role`}
                  contentFieldName={`config.messages.${idx}.content`}
                  appliedVariableData={appliedVariableData}
                  handleLabelsAdd={handleLabelsAdd}
                  index={idx}
                />
              );
            })}

          <Box sx={{ display: "flex", justifyContent: "space-between" }}>
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="mdi:plus" />}
              onClick={() => {
                append({
                  id: getRandomId(),
                  role: "user",
                  content: [
                    {
                      type: "text",
                      text: "",
                    },
                  ],
                });
                trackEvent(Events.userAddMessage);
              }}
            >
              Add Message
            </Button>
            <Typography
              color="text.secondary"
              variant="subtitle2"
              fontWeight={400}
            >
              use
              <Typography component="span" color="primary">
                {" {{ "}
              </Typography>
              to access variables
            </Typography>
          </Box>
        </Box>
      )}
    </Box>
  );
};

export default Workbench;

Workbench.propTypes = {
  control: PropTypes.any,
  loading: PropTypes.bool,
  handleLabelsAdd: PropTypes.func,
  appliedVariableData: PropTypes.object,
};
