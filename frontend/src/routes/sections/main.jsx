/* eslint-disable react-refresh/only-export-components */

import React from "react";
import lazyWithRetry from "src/utils/lazyWithRetry";

// ----------------------------------------------------------------------

const Page404 = lazyWithRetry(() => import("src/pages/404"));
const DatasetExperimentsMock = import.meta.env.DEV
  ? lazyWithRetry(
      () =>
        import(
          "src/pages/dashboard/dataset-experiments/DatasetExperimentsMock"
        ),
    )
  : null;

const developmentPreviewRoutes = import.meta.env.DEV
  ? [
      {
        path: "design/dataset-experiments",
        element: <DatasetExperimentsMock />,
      },
    ]
  : [];

// ----------------------------------------------------------------------

export const mainRoutes = [
  {
    // element: (
    //   <CompactLayout>
    //     <Outlet />
    //   </CompactLayout>
    // ),
    children: [
      { path: "404", element: <Page404 /> },
      ...developmentPreviewRoutes,
    ],
  },
];
