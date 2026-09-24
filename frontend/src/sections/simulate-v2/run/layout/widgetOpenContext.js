import { createContext } from "react";

/* Set by SortablePanel for the widget it wraps, read by the panel card so its
   title opens the widget editor — the same "click a widget to open it" the
   dashboards grid has. Null outside a sortable panel (print, drag overlay). */
export const WidgetOpenContext = createContext(null);

/* Provided once by the analytics shell: the runs + catalogue widget queries
   read, and the navigation to the full-page editor. */
export const SimWidgetsContext = createContext(null);
