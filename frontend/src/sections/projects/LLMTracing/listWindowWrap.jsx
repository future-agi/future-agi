import React from "react";
import { ListWindowEmptyState } from "./listWindowEmptyState";

// NoRowsOverlay's `wrap`: the plain empty state becomes the fallback.
export const listWindowWrap = ({ noun, listWindow }) =>
  function wrapListWindow(plain) {
    return (
      <ListWindowEmptyState
        noun={noun}
        listWindow={listWindow}
        fallback={plain}
      />
    );
  };
