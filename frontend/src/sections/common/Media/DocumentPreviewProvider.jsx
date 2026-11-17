import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation } from "react-router-dom";
import MediaLightbox from "./MediaLightbox";

const DocumentPreviewContext = createContext(null);

export const useDocumentPreview = () => useContext(DocumentPreviewContext);

// Keep both the selected document and its dialog outside disposable grid
// cells. A portal alone still unmounts when its cell is redrawn.
export default function DocumentPreviewProvider({ children }) {
  const { pathname } = useLocation();
  const [preview, setPreview] = useState(null);
  const openPreview = useCallback(
    (mediaRef) => setPreview({ mediaRef, pathname }),
    [pathname],
  );
  // Row selection changes the query string, not the page. Navigation must
  // close the preview and must not resurrect it when returning to the page.
  useEffect(() => setPreview(null), [pathname]);
  const item = preview?.pathname === pathname ? preview.mediaRef : null;

  return (
    <DocumentPreviewContext.Provider value={openPreview}>
      {children}
      <MediaLightbox
        open={Boolean(item)}
        items={item ? [item] : []}
        onClose={() => setPreview(null)}
      />
    </DocumentPreviewContext.Provider>
  );
}

DocumentPreviewProvider.propTypes = { children: PropTypes.node };
