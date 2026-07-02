import React from "react";
import { Helmet } from "react-helmet-async";
import KnowledgeBaseSheetView from "src/sections/knowledge-base/sheet-view/knowledge-base-sheet-view";

export default function KnowledgeBaseDetailView() {
  return (
    <>
      <Helmet>
        <title>Knowledge Base Sheet View</title>
      </Helmet>
      <KnowledgeBaseSheetView />
    </>
  );
}
