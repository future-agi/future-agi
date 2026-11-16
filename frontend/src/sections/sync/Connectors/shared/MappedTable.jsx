import {
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const MappedTable = ({ mappedData }) => {
  const renderBody = () => {
    if (!mappedData) {
      return (
        <TableRow>
          <TableCell colSpan={3}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                paddingTop: "100px",
              }}
            >
              <Box
                component="img"
                alt="empty content"
                src={"/assets/icons/components/ic_extra_scroll.svg"}
                sx={{ width: 1, maxWidth: 220 }}
              />
              <Typography
                variant="subtitle1"
                sx={{ width: "250px", textAlign: "center" }}
              >
                Fill out your left hand panel to connect your data
              </Typography>
            </Box>
          </TableCell>
        </TableRow>
      );
    }

    return mappedData?.map(({ input, output }) => (
      <TableRow key={input}>
        <TableCell>{input}</TableCell>
        <TableCell>
          {output?.length ? (
            <svg
              width="90"
              height="8"
              viewBox="0 0 90 8"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M0.533334 4C0.533334 5.91459 2.08541 7.46667 4 7.46667C5.91459 7.46667 7.46667 5.91459 7.46667 4C7.46667 2.08541 5.91459 0.533334 4 0.533334C2.08541 0.533334 0.533334 2.08541 0.533334 4ZM90 4L83.5 0.247223V7.75278L90 4ZM4 4.65H84.15V3.35H4V4.65Z"
                fill="divider"
              />
            </svg>
          ) : (
            <></>
          )}
        </TableCell>
        <TableCell>{output}</TableCell>
      </TableRow>
    ));
  };

  return (
    <TableContainer
      elevation={0}
      component={Paper}
      sx={{ flex: 1, overflow: "auto" }}
    >
      <Table sx={{}} stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell>Input Column</TableCell>
            <TableCell />
            <TableCell>Mapped Field</TableCell>
          </TableRow>
        </TableHead>
        <TableBody sx={{}}>{renderBody()}</TableBody>
      </Table>
    </TableContainer>
  );
};

MappedTable.propTypes = {
  mappedData: PropTypes.object,
};

export default MappedTable;
