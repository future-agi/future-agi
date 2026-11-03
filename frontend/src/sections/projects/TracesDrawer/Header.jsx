import { LoadingButton } from "@mui/lab";
import { IconButton, Stack, Typography, Skeleton } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";

export default function Header({
  rowData,
  onClose,
  hasNextTrace,
  hasPrevTrace,
  handleNextTrace,
  handlePrevTrace,
  isNextLoading,
  isPrevLoading,
  initialLoading,
}) {
  const isAnyLoading = isNextLoading || isPrevLoading || initialLoading;

  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"flex-start"}
    >
      <Stack direction={"column"} gap={0}>
        <Typography
          typography="m3"
          fontWeight={"fontWeightSemiBold"}
          sx={{
            color: "text.primary",
          }}
        >
          Session
        </Typography>
        <Stack direction={"row"} alignItems={"center"} gap={1.5}>
          {isAnyLoading ? (
            <Skeleton variant="text" width={200} height={24} />
          ) : (
            <>
              <Typography
                typography={"s2_1"}
                fontWeight={"fontWeightRegular"}
                color={"text.primary"}
              >
                {rowData?.session_id ? rowData?.session_id : "N/A"}
              </Typography>
              <ShowComponent condition={rowData?.session_id}>
                <IconButton
                  onClick={() => {
                    navigator.clipboard.writeText(rowData?.session_id);
                    enqueueSnackbar("Session Id copied");
                  }}
                  size="small"
                >
                  <SvgColor
                    src={"/assets/icons/ic_copy.svg"}
                    sx={{
                      bgcolor: "text.primary",
                      height: "16px",
                      width: "16px",
                    }}
                  />
                </IconButton>
              </ShowComponent>
            </>
          )}
        </Stack>
      </Stack>
      <Stack direction={"row"} alignItems={"center"} gap={1.5}>
        <LoadingButton
          loading={isPrevLoading}
          disabled={!hasPrevTrace || isNextLoading}
          onClick={handlePrevTrace}
          size="small"
          variant="outlined"
          startIcon={
            <SvgColor
              width="20px"
              sx={{
                bgcolor:
                  !hasPrevTrace || isNextLoading || isPrevLoading
                    ? "divider"
                    : "text.primary",
                rotate: "180deg",
              }}
              src={"/assets/icons/custom/lucide--chevron-right.svg"}
            />
          }
        >
          Prev
        </LoadingButton>
        <LoadingButton
          loading={isNextLoading}
          disabled={!hasNextTrace || isPrevLoading}
          onClick={handleNextTrace}
          size="small"
          variant="outlined"
          startIcon={
            <SvgColor
              width="20px"
              sx={{
                bgcolor:
                  !hasNextTrace || isPrevLoading || isNextLoading
                    ? "divider"
                    : "text.primary",
              }}
              src={"/assets/icons/custom/lucide--chevron-right.svg"}
            />
          }
        >
          Next
        </LoadingButton>
        <IconButton onClick={onClose}>
          <SvgColor
            src={"/assets/icons/ic_close.svg"}
            sx={{
              height: "24px",
              width: "24px",
              bgcolor: "text.primary",
            }}
          />
        </IconButton>
      </Stack>
    </Stack>
  );
}

Header.propTypes = {
  rowData: PropTypes.object,
  onClose: PropTypes.func,
  hasNextTrace: PropTypes.bool,
  hasPrevTrace: PropTypes.bool,
  handleNextTrace: PropTypes.func,
  handlePrevTrace: PropTypes.func,
  isNextLoading: PropTypes.bool,
  isPrevLoading: PropTypes.bool,
  initialLoading: PropTypes.bool,
};
