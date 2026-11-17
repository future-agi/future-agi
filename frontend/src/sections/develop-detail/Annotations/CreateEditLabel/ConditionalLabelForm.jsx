// @ts-nocheck
import PropTypes from "prop-types";
import React from "react";
import { useWatch } from "react-hook-form";
import TextLabelForm from "./TextLabelForm";
import NumericLabelForm from "./NumericLabelForm";
import StarsLabelForm from "./StarsLabelForm";
import CategoricalLabelForm from "./CategoricalLabelForm";

const ConditionalLabelForm = ({
  control,
  storedDisplayType,
  placeholderRequired,
}) => {
  const annotationType = useWatch({ control, name: "type" });

  if (annotationType === "text") {
    return (
      <TextLabelForm
        control={control}
        placeholderRequired={placeholderRequired}
      />
    );
  }

  if (annotationType === "numeric") {
    return (
      <NumericLabelForm
        control={control}
        storedDisplayType={storedDisplayType}
      />
    );
  }

  if (annotationType === "star") {
    return <StarsLabelForm />;
  }

  if (annotationType === "categorical") {
    return <CategoricalLabelForm control={control} />;
  }

  return <></>;
};

ConditionalLabelForm.propTypes = {
  control: PropTypes.any,
  storedDisplayType: PropTypes.string,
  placeholderRequired: PropTypes.bool,
};

export default ConditionalLabelForm;
