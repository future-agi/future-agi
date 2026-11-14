import React, { useState } from "react";
import { Box, Typography, Input, IconButton, Checkbox } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { StyledBox, WrapperBox } from "./AnnotationsStyle";

const SearchModal = ({ peopleAssigned, onItemClick, isCheckable }) => {
  const [searchQuery, setSearchQuery] = useState("");
  const handleSearchChange = (event) => {
    setSearchQuery(event.target.value.toLowerCase());
  };

  const filteredPeople = peopleAssigned?.filter((person) =>
    person.name.toLowerCase().includes(searchQuery),
  );

  return (
    <WrapperBox>
      <StyledBox>
        <IconButton size="small">
          <Iconify icon="solar:magnifier-linear" color="divider" />
        </IconButton>
        <Input
          placeholder="Search annotators"
          disableUnderline
          onChange={handleSearchChange}
        />
      </StyledBox>
      <Typography
        color="text.secondary"
        fontSize={12}
        fontWeight={600}
        padding={0.5}
        paddingTop={1}
      >
        All Annotators
      </Typography>
      <Box
        sx={{
          height: "200px",
          overflowY: "auto",
        }}
      >
        {filteredPeople?.map((i, index) => (
          <Box
            display="flex"
            alignItems="center"
            gap={1}
            sx={{
              position: "relative",
              "&:hover": {
                backgroundColor: "action.hover",
                borderRadius: "8px",
              },
            }}
            key={index}
            padding={1}
          >
            <Box display="flex" alignItems="center" gap={1}>
              {isCheckable && <Checkbox />}
              <img
                src={"https://cdn-icons-png.flaticon.com/512/149/149071.png"}
                alt={i.name}
                onClick={onItemClick}
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  objectFit: "cover",
                }}
              />
              <Typography
                onClick={onItemClick}
                color="text.primary"
                fontSize={12}
              >
                {i.name}
              </Typography>
            </Box>
          </Box>
        ))}
      </Box>
    </WrapperBox>
  );
};

SearchModal.propTypes = {
  peopleAssigned: PropTypes.array,
  onItemClick: PropTypes.func,
  isCheckable: PropTypes.bool,
};

export default SearchModal;
