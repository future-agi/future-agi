// AG Grid set-up: modules, licence and the shared pager label.
//
// Imported by every file that renders an AgGridReact, not by main.jsx, so the
// grid code (~400 KB gzipped) loads with the first grid instead of before the
// first paint. ES modules run this once however many grids import it.
import {
  CellSelectionModule,
  LicenseManager,
  ClipboardModule,
  MasterDetailModule,
  MenuModule,
  RichSelectModule,
  ServerSideRowModelApiModule,
  ServerSideRowModelModule,
  StatusBarModule,
} from "ag-grid-enterprise";
import {
  AllCommunityModule,
  ModuleRegistry,
  provideGlobalGridOptions,
} from "ag-grid-community";
import AgGridLoadingOverlay from "src/components/ag-grid/AgGridLoadingOverlay";

ModuleRegistry.registerModules([
  AllCommunityModule,
  ServerSideRowModelModule,
  ServerSideRowModelApiModule,
  StatusBarModule,
  MasterDetailModule,
  RichSelectModule,
  MenuModule,
  ClipboardModule,
  CellSelectionModule,
]);

// AG Grid's built-in pager says "Rows per page:", same as DataTablePagination.
// Loading is the product's table skeleton, not AG Grid's "Loading..." popup;
// a grid that sets its own loadingOverlayComponent keeps it. Server-side rows still being fetched keep AG Grid's own loading cell: grids
// such as Users and Tracing put status text in it through getLocaleText.
provideGlobalGridOptions(
  {
    localeText: { pageSizeSelectorLabel: "Rows per page:" },
    loadingOverlayComponent: AgGridLoadingOverlay,
  },
  "deep",
);

LicenseManager.setLicenseKey(import.meta.env.VITE_AG_GRID_LICENSE_KEY);
