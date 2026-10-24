import EmptyContent from "src/components/empty-content";
import React from "react";

const EmptyVariable = () => (
  <EmptyContent
    icon="solar:code-square-linear"
    description={
      <>
        Create variables within the prompt using double curly braces {"{{}}"} to
        insert the datapoints.
      </>
    }
    sx={{ width: "100%" }}
  />
);

export default EmptyVariable;
