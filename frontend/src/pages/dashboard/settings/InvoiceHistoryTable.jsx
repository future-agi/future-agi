import React, { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import { Box, IconButton, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

const defaultColDef = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const InvoiceHistoryTable = ({ userData }) => {
  const theme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);

  // const handleDownload = (invoiceId) => {
  //   axiosInstance.post(endpoints.stripe.downloadInvoice, {
  //     invoice_id: invoiceId
  //   }).then((res) => {
  //     const invoicePdfUrl = res?.data?.result?.invoicePdfUrl;
  //     window.open(invoicePdfUrl, '_blank');
  //   }).catch((error) => {
  //     console.error("Error downloading invoice: ", error);
  //   });
  // };

  const handleReceiptClick = (receiptUrl) => {
    if (receiptUrl) {
      window.open(receiptUrl, "_blank");
    }
  };

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Billing Date",
        field: "date",
        flex: 1,
        sortable: false,
        valueFormatter: (params) => {
          if (!params.value) return "";
          const date = new Date(params.value);
          return date.toLocaleDateString("en-US", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
          });
        },
      },
      {
        headerName: "Amount",
        field: "amount",
        cellStyle: { fontVariantNumeric: "tabular-nums" },
        flex: 1,
        sortable: false,
        valueFormatter: (params) => {
          if (!params.value) return "";
          return `$${params.value}`;
        },
      },
      {
        headerName: "Payment Method",
        field: "payment_type",
        flex: 1,
        sortable: false,
      },
      {
        headerName: "Action",
        field: "download",
        flex: 1,
        sortable: false,
        cellRenderer: (params) => (
          <IconButton
            // onClick={() => handleDownload(params.data.id)}
            onClick={() => handleReceiptClick(params.data.receipt_url)}
            size="small"
            sx={{
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Icon
              icon="solar:download-minimalistic-linear"
              width={16}
              height={16}
            />
          </IconButton>
        ),
      },
    ],
    [],
  );

  const gridOptions = {
    pagination: true,
    paginationAutoPageSize: true,
  };

  return (
    <Box sx={{ height: "500px", width: "100%", marginTop: theme.spacing(3) }}>
      <Box className="ag-theme-quartz" style={{ height: "100%" }}>
        <AgGridReact
          theme={agTheme}
          columnDefs={columnDefs}
          rowData={userData?.result?.invoices || []}
          defaultColDef={defaultColDef}
          pagination={gridOptions.pagination}
          paginationAutoPageSize={gridOptions.paginationAutoPageSize}
          suppressRowClickSelection={true}
          paginationPageSizeSelector={false}
          suppressContextMenu={true}
          serverSideInitialRowCount={5}
          animateRows={true}
          rowHeight={44}
          headerHeight={50}
        />
      </Box>
    </Box>
  );
};

InvoiceHistoryTable.propTypes = {
  userData: PropTypes.object,
};

export default InvoiceHistoryTable;
