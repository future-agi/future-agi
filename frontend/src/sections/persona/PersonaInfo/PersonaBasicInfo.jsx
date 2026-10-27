import { Box, Typography, Divider } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import PersonaIcons from "../PersonaIcons";
import { extractGenderAgeLocationTagsFromPersona } from "../common";

const PersonaBasicInfo = ({ persona }) => {
  const genderAgeLocationTags =
    extractGenderAgeLocationTagsFromPersona(persona);
  return (
    <Box
      sx={{
        padding: "1px",
        borderRadius: 0.5,
      }}
    >
      <Box
        sx={{
          padding: 2,
          backgroundColor: "background.neutral",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          height: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <PersonaIcons
              imgSrc="/assets/icons/custom/persona.svg"
              imgStyles={{ width: "24px", height: "24px" }}
            />
            <Typography typography="s1_2" fontWeight="fontWeightMedium">
              {persona?.name}
            </Typography>
          </Box>
        </Box>

        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography
            typography="s1_2"
            color="text.primary"
            sx={{
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {persona?.description}
          </Typography>
          <ShowComponent condition={genderAgeLocationTags.length > 0}>
            <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
              {genderAgeLocationTags.map((tag) => (
                <Box
                  key={tag[0]}
                  sx={{
                    padding: "4px 12px",
                    borderRadius: "2px",
                    border: "1px solid",
                    borderColor: "action.focus",
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                    background: "background.paper",
                  }}
                >
                  <Typography typography="s2" fontWeight="fontWeightMedium">
                    {tag[0]}:
                  </Typography>
                  <Typography typography="s2" fontWeight="fontWeightNormal">
                    {tag[1]}
                  </Typography>
                </Box>
              ))}
            </Box>
          </ShowComponent>
        </Box>
        {persona?.occupation && <Divider flexItem orientation="horizontal" />}
        <ShowComponent condition={persona?.occupation}>
          {" "}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <PersonaIcons
              imgSrc="/assets/icons/persona/profession.svg"
              imgStyles={{ width: "20px", height: "20px" }}
            />
            <Typography
              fontSize="15px"
              fontWeight="fontWeightMedium"
              lineHeight="22px"
            >
              {persona?.occupation?.join(", ")}
            </Typography>
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

PersonaBasicInfo.propTypes = {
  viewOptions: PropTypes.shape({
    name: PropTypes.bool,
    description: PropTypes.bool,
  }),
  multiple: PropTypes.bool,

  title: PropTypes.string,
  persona: PropTypes.object,
};

export default PersonaBasicInfo;
