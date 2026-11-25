// import { Box, Skeleton } from '@mui/material'
// import React from 'react'

// function AnnotationTabSkeleton() {
//   return (
//     <Box sx={{ width: "100%" }} padding={1}>
//       {[1, 2, 3, 4, 5, 6,7,8,9,10].map((item, index) => (
//         <Box
//           key={index}
//           sx={{
//             display: "flex",
//             flexDirection: "row",
//             gap: "10px",
//             mb: 2,
//           }}
//         >
//           <Skeleton variant="rectangular" animation="wave" width="40%" height={50} />
//           <Skeleton variant="rectangular" animation="wave" width="60%" height={50} />
//           <Skeleton variant="rectangular" animation="wave" width="60%" height={50} />
//           <Skeleton variant="rectangular" animation="wave" width="60%" height={50} />
//           <Skeleton variant="rectangular" animation="wave" width="60%" height={50} />
//         </Box>
//       ))}
//     </Box>
//   )
// }

// export default AnnotationTabSkeleton

import React, { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import "src/utils/ag-grid-setup";
import { Box, Skeleton } from "@mui/material";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

const AnnotationTabSkeleton = () => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const skeletonCellRenderer = () => {
    const randomWidth = Math.floor(Math.random() * (100 - 60 + 1)) + 60;

    return (
      <div style={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Skeleton
          variant="rounded"
          animation="wave"
          height={11}
          width={`${randomWidth}%`}
          sx={{
            bgcolor: "action.hover",
          }}
        />
      </div>
    );
  };

  const columnDefs = useMemo(
    () => [
      {
        headerName: "View Name",
        field: "col1",
        width: 266,
        cellRenderer: skeletonCellRenderer,
      },
      {
        headerName: "No.of Annotations",
        field: "col2",
        flex: 1,
        width: 247,
        cellRenderer: skeletonCellRenderer,
      },
      {
        headerName: "Status",
        field: "col3",
        flex: 1.5,
        width: 373,
        cellRenderer: skeletonCellRenderer,
      },
      {
        headerName: "People Assigned",
        field: "col4",
        flex: 1.5,
        width: 372,
        cellRenderer: skeletonCellRenderer,
      },
      {
        headerName: "Created At",
        field: "col5",
        flex: 1,
        width: 247,
        cellRenderer: skeletonCellRenderer,
      },
      {
        headerName: "Actions",
        field: "col6",
        flex: 1,
        width: 251,
        cellRenderer: skeletonCellRenderer,
      },
    ],
    [],
  );

  const rowData = useMemo(
    () =>
      Array.from({ length: 15 }, (_) => ({
        col1: null,
        col2: null,
        col3: null,
        col4: null,
        col5: null,
        col6: null,
      })),
    [],
  );

  const gridOptions = {
    rowSelection: "multiple",
    suppressRowClickSelection: true,
  };

  return (
    <Box sx={{ height: 500, width: "100%", p: 2 }}>
      <AgGridReact
        columnDefs={columnDefs}
        rowData={rowData}
        domLayout="autoHeight"
        theme={agTheme}
        gridOptions={gridOptions}
      />
    </Box>
  );
};

export default AnnotationTabSkeleton;
