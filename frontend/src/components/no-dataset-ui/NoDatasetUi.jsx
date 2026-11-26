import EmptyContent from "src/components/empty-content";
import React from "react";

export default function NoDataSetUi() {
  return (
    <EmptyContent
      title="No Datasets added"
      description="Add and work with datasets by clicking on add dataset button"
      sx={{ minHeight: 400 }}
    />
  );
}
