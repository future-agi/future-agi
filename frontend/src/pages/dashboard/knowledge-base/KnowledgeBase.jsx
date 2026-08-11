import React from "react";
import { Helmet } from "react-helmet-async";
import KnowledgeBaseView from "src/sections/knowledge-base/KnowledgeBaseView";

const KnowledgeBase = () => {
  return (
    <>
      <Helmet>
        <title>Knowledge Base</title>
      </Helmet>
      <KnowledgeBaseView />
    </>
  );
};

export default KnowledgeBase;
