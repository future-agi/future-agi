import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import AddAnnotationsDrawer from "src/components/traceDetailDrawer/add-annotations-drawer";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { transformLabelObject } from "src/sections/develop-detail/Annotations/CreateEditLabel/common";
import AnnotationFieldWrapper from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldWrapper";
import { useForm } from "react-hook-form";
import _ from "lodash";
import { Events, trackEvent } from "src/utils/Mixpanel";

import { OutlinedButton } from "../ProjectDetailComponents";

const AnnotateSection = ({
  annotationLabels,
  annotationValues,
  projectVersionId,
  traceId,
}) => {
  const [isAddAnnotationOpen, setIsAddAnnotationOpen] = useState(false);
  const { projectId } = useParams();

  const { mutate: addAnnotationValues } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.addAnnotationValues(), data),
  });

  const getDefaultValues = () => {
    const obj = {};
    annotationLabels.map((label) => {
      obj[label.id] = annotationValues?.[label.id] || "";
    });
    return obj;
  };

  const { control, handleSubmit, watch } = useForm({
    defaultValues: getDefaultValues(),
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const onSubmit = React.useCallback(
    _.debounce((data) => {
      //@ts-ignore
      addAnnotationValues({
        trace_id: traceId,
        annotation_values: Object.fromEntries(
          Object.entries(data).filter(([_, value]) => value !== ""),
        ),
      });
    }, 500),
    [],
  );

  useEffect(() => {
    const subscription = watch(handleSubmit(onSubmit));
    return () => subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleSubmit, watch]);

  return (
    <Box
      sx={{
        padding: "14px",
        flex: 1,
        overflowX: "hidden",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography color="text.disabled" fontSize="13px" fontWeight={600}>
          Human Annotation
        </Typography>
        <OutlinedButton
          size="small"
          variant="outlined"
          onClick={() => {
            setIsAddAnnotationOpen(true);
            trackEvent(Events.modifyAnnotationsClicked);
          }}
        >
          Configure
        </OutlinedButton>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          overflowY: "auto",
        }}
      >
        {annotationLabels?.map((item, index) => {
          const transformedItem = transformLabelObject(item);

          return (
            <AnnotationFieldWrapper
              index={index}
              key={transformedItem.id}
              labelName={transformedItem.name}
              type={transformedItem.type}
              settings={transformedItem.settings}
              control={control}
              fieldName={transformedItem.id}
            />
          );
        })}
      </Box>
      <AddAnnotationsDrawer
        open={isAddAnnotationOpen}
        onClose={() => setIsAddAnnotationOpen(false)}
        projectVersionId={projectVersionId}
        selectedAnnotationLabels={annotationLabels}
        projectId={projectId}
      />
    </Box>
  );
};

AnnotateSection.propTypes = {
  annotationLabels: PropTypes.array,
  annotationValues: PropTypes.array,
  projectVersionId: PropTypes.string,
  traceId: PropTypes.string,
};

export default AnnotateSection;
