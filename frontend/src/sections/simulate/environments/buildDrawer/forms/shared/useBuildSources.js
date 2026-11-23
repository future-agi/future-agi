import { useState } from "react";
import { useFileUploads } from "./useFileUploads";

export default function useBuildSources() {
  const uploads = useFileUploads();
  const [knowledge, setKnowledge] = useState([]);
  const [policy, setPolicy] = useState({});
  const addKnowledgeSelection = (selection, label) => {
    setKnowledge((current) => [
      ...current.filter((item) => item.selection.kb_id !== selection.kb_id),
      { selection, label },
    ]);
  };
  const documentIds = uploads.done.map(({ documentId }) => documentId);
  const selection =
    documentIds.length || knowledge.length
      ? {
          document_ids: documentIds,
          document_purposes: Object.fromEntries(
            documentIds.filter((id) => policy[id]).map((id) => [id, "policy"]),
          ),
          knowledge_base: knowledge.map(({ selection: item }) => item),
        }
      : undefined;

  return {
    uploads,
    knowledge,
    policy,
    setPolicy,
    selection,
    blocked: Boolean(uploads.uploading.length || uploads.failed.length),
    clear: () => {
      uploads.files.forEach(({ id }) => uploads.remove(id));
      setKnowledge([]);
      setPolicy({});
    },
    addKnowledgeSelection,
    addKnowledge: ({ kb, files, whole_collection: wholeCollection }) => {
      const selection = {
        kind: "knowledge_base",
        kb_id: kb.id,
        ...(wholeCollection
          ? { whole_collection: true }
          : { file_ids: files.map(({ id }) => id) }),
      };
      addKnowledgeSelection(
        selection,
        wholeCollection
          ? kb.name
          : `${kb.name} (${files.length} file${files.length === 1 ? "" : "s"})`,
      );
    },
    removeKnowledge: (id) => {
      setKnowledge((current) =>
        current.filter(({ selection }) => selection.kb_id !== id),
      );
    },
  };
}
