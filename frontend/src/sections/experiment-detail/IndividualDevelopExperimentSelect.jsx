// import {
//     Box,
//     InputAdornment,
//     MenuItem,
//     Popover,
//     Select,
//     Skeleton,
//     styled,
//     TextField,
//     Typography,
//   } from "@mui/material";
//   import React, { useEffect, useMemo, useRef, useState } from "react";
//   import { useNavigate, useParams } from "react-router";
//   import { useExperimentList } from "src/api/develop/experiment-detail";
//   import Iconify from "src/components/iconify";
//   import { useDebounce } from "src/hooks/use-debounce";
// import axios , { endpoints } from "src/utils/axios";

//   const DevelopSelect = styled(Select)(({ theme }) => ({
//     "& .MuiSelect-select": {
//       paddingTop: 4,
//       paddingBottom: 4,
//       color: theme.palette.text.primary,
//       fontWeight: 500,
//     },
//   }));

//   const IndividualDevelopExperimentSelect = () => {
//     const [experimentList , setExperimentList] = useState();
//     const [selectOpen, setSelectOpen] = useState(false);
//     const anchorRef = useRef(null);
//     const navigate = useNavigate();
//     const { individualExperimentId } = useParams();

//     const [searchText, setSearchText] = useState("");

//     const debouncedSearchText = useDebounce(searchText, 500);

//     const { isLoading: isExperimentListLoading } =
//       useExperimentList(debouncedSearchText);
//     useEffect(()=>{
//       const id = localStorage.getItem('derivedDatasetId');
//       const fetchData = async () => {
//         try {
//             const response = await axios.get(`${endpoints.develop.getDerivedDatasets()}${id}/`);
//             setExperimentList(response?.data?.result);
//         } catch (err) {
//             console.error('Error fetching data:', err);
//         }
//     };
//     fetchData();
//     },[]);

//     const experimentOptions = useMemo(
//       () =>
//         experimentList?.map(({ id, name }) => ({
//           label: name,
//           value: id,
//         })),
//       [experimentList],
//     );

//     if (isExperimentListLoading || !experimentOptions) {
//       return <Skeleton variant="text" width={100} height={30} />;
//     }

//     return (
//       <>
//         <DevelopSelect
//           size="small"
//           open={selectOpen}
//           onOpen={() => setSelectOpen(true)}
//           onClose={() => setSelectOpen(false)}
//           ref={anchorRef}
//           MenuProps={{
//             PaperProps: {
//               style: {
//                 display: "none",
//               },
//             },
//           }}
//           value={individualExperimentId}
//         >
//           <MenuItem value={individualExperimentId}>
//             {experimentOptions?.find((o) => o.value === individualExperimentId)?.label}
//           </MenuItem>
//         </DevelopSelect>
//         <Popover
//           open={selectOpen}
//           anchorEl={anchorRef.current}
//           onClose={() => setSelectOpen(false)}
//           anchorOrigin={{
//             vertical: "bottom",
//             horizontal: "right",
//           }}
//           transformOrigin={{
//             vertical: "top",
//             horizontal: "right",
//           }}

//           PaperProps={{
//             sx: {
//               minWidth: anchorRef.current?.clientWidth,
//             },
//           }}
//         >
//           <Box>
//             <TextField
//               placeholder="Search Dataset"
//               size="small"
//               value={searchText}
//               onChange={(e) => setSearchText(e.target.value)}
//               InputProps={{
//                 startAdornment: (
//                   <InputAdornment position="start">
//                     <Iconify icon="eva:search-fill" sx={{ color: "divider" }} />
//                   </InputAdornment>
//                 ),
//               }}
//               fullWidth
//             />
//             <Typography
//               sx={{ paddingX: 1, paddingTop: 1 }}
//               color="text.disabled"
//               fontWeight={600}
//               fontSize={12}
//             >
//               All Experiments
//             </Typography>
//             <Box sx={{ maxHeight: "220px", overflowY: "auto" }}>
//               {experimentOptions?.map((option) => (
//                 <MenuItem
//                   key={option.value}
//                   value={option.value}
//                   onClick={() => {
//                     navigate(`/dashboard/develop/individual-experiment/${option.value}`);
//                     setSelectOpen(false);
//                   }}
//                 >
//                   {option.label}
//                 </MenuItem>
//               ))}
//             </Box>
//           </Box>
//         </Popover>
//       </>
//     );
//   };

//   export default IndividualDevelopExperimentSelect;

import { Box, MenuItem, Typography } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useExperimentList } from "src/api/develop/experiment-detail";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import DropdownWithSearch from "../common/DropdownWithSearch";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import logger from "src/utils/logger";

const IndividualDevelopExperimentSelect = () => {
  const [experimentList, setExperimentList] = useState();
  const [, setSelectOpen] = useState(false);
  const anchorRef = useRef(null);
  const navigate = useNavigate();
  const { individualExperimentId } = useParams();

  const [searchText, setSearchText] = useState("");

  const debouncedSearchText = useDebounce(searchText.trim(), 500);

  useExperimentList(debouncedSearchText);

  useEffect(() => {
    const id = localStorage.getItem("derivedDatasetId");
    const fetchData = async () => {
      try {
        const response = await axios.get(
          endpoints.develop.getDerivedDatasets(id),
        );
        setExperimentList(response?.data?.result);
      } catch (err) {
        logger.error("Error fetching data:", err);
      }
    };
    fetchData();
  }, []);

  const experimentOptions = useMemo(
    () =>
      experimentList?.map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [experimentList],
  );

  const filteredOptions = useMemo(() => {
    if (!searchText) return experimentOptions;
    return experimentOptions?.filter((option) =>
      option.label.toLowerCase().includes(searchText.toLowerCase()),
    );
  }, [searchText, experimentOptions]);

  // if (isExperimentListLoading || !experimentOptions) {
  //   return <Skeleton variant="text" width={100} height={30} />;
  // }

  const handleSelect = (value) => {
    navigate(`/dashboard/develop/individual-experiment/${value}`);
  };

  const renderValue = (value) => {
    if (!experimentOptions) {
      return "Select an experiment";
    }
    const selectedOption = experimentOptions.find(
      (option) => option.value === value,
    );
    return selectedOption ? selectedOption.label : "Select an experiment";
  };

  const renderPopover = ({ onClose, anchorElement }) => (
    <Box>
      <FormSearchField
        placeholder="Search Dataset"
        size="small"
        searchQuery={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        fullWidth
        onFocus={(e) => e.stopPropagation()}
        onBlur={(e) => {
          if (!anchorElement?.contains(e.relatedTarget)) {
            onClose();
          }
        }}
      />
      <Typography
        sx={{ paddingX: 1, paddingTop: 1 }}
        color="text.disabled"
        fontWeight={600}
        fontSize={12}
      >
        All Experiments
      </Typography>
      <Box sx={{ maxHeight: "220px", overflowY: "auto" }}>
        {filteredOptions?.map((option) => (
          <MenuItem
            key={option.value}
            value={option.value}
            onClick={() => {
              handleSelect(option.value);
              onClose();
            }}
          >
            {option.label}
          </MenuItem>
        ))}
      </Box>
    </Box>
  );

  return (
    <DropdownWithSearch
      value={individualExperimentId}
      options={experimentOptions}
      onSelect={handleSelect}
      anchorRef={anchorRef}
      onClose={() => setSelectOpen(false)}
      popoverComponent={renderPopover}
      size="small"
      renderValue={renderValue}
    />
  );
};

export default IndividualDevelopExperimentSelect;
