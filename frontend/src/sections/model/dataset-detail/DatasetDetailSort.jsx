import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { MenuItem, Paper, Popper, styled } from "@mui/material";
import Iconify from "src/components/iconify";
import { isMouseInBound } from "src/utils/utils";

const MenuPaper = styled(Paper)(() => ({
  maxHeight: "calc(100% - 96px)",
  WebkitOverflowScrolling: "touch",
  padding: "4px",
  // Add any additional styles you want to match the Menu appearance
}));

const DatasetDetailSort = ({
  open,
  onClose,
  anchorEl,
  sortOrder,
  setSortOrder,
  sortKey,
  setSortKey,
  metricList,
}) => {
  const [ancElem, setAncElm] = useState(null);
  const popperRef = useRef(null);
  const secondaryMenuRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (popperRef.current && !popperRef.current?.contains(event.target)) {
        if (
          secondaryMenuRef.current &&
          secondaryMenuRef.current.contains(event.target)
        )
          return;
        onClose();
        setAncElm(null);
      }
    }

    // Bind the event listener
    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      // Unbind the event listener on clean up
      document.removeEventListener("mousedown", handleClickOutside);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [popperRef]);

  const onMouseLeave = (e) => {
    if (isMouseInBound(e, secondaryMenuRef.current.getBoundingClientRect()))
      return;
    setAncElm(null);
  };

  return (
    <>
      <Popper
        open={open}
        ref={popperRef}
        anchorEl={anchorEl}
        placement="bottom-end"
        // anchorOrigin={{
        //   vertical: "bottom",
        //   horizontal: "right",
        // }}
        // transformOrigin={{
        //   vertical: "top",
        //   horizontal: "right",
        // }}
      >
        <MenuPaper elevation={8}>
          <MenuItem
            onClick={(e) => setAncElm({ a: e.target, order: "asc" })}
            onMouseEnter={(e) => setAncElm({ a: e.target, order: "asc" })}
            onMouseLeave={onMouseLeave}
          >
            Lowest to highest score
            <Iconify
              sx={{ width: "11px" }}
              icon="solar:alt-arrow-right-linear"
            />
          </MenuItem>
          <MenuItem
            onClick={(e) => setAncElm({ a: e.target, order: "desc" })}
            onMouseEnter={(e) => setAncElm({ a: e.target, order: "desc" })}
            onMouseLeave={onMouseLeave}
          >
            Highest to lowest score
            <Iconify
              sx={{ width: "11px" }}
              icon="solar:alt-arrow-right-linear"
            />
          </MenuItem>
          <MenuItem
            selected={sortKey == "createdAt" && sortOrder == "desc"}
            onClick={() => {
              setSortKey("createdAt");
              setSortOrder("desc");
              onClose();
            }}
          >
            Latest
          </MenuItem>
          <MenuItem
            selected={sortKey == "createdAt" && sortOrder == "asc"}
            onClick={() => {
              setSortKey("createdAt");
              setSortOrder("asc");
              onClose();
            }}
          >
            Earliest
          </MenuItem>
        </MenuPaper>
      </Popper>

      <Popper
        open={Boolean(ancElem)}
        anchorEl={ancElem?.a}
        placement="left-start"
      >
        <MenuPaper elevation={8} ref={secondaryMenuRef}>
          {metricList?.map(({ id, name }) => (
            <MenuItem
              selected={sortKey == id && sortOrder == ancElem?.order}
              key={id}
              onClick={() => {
                setSortOrder(ancElem.order);
                setSortKey(id);
                setAncElm(null);
                onClose();
              }}
            >
              {name}
            </MenuItem>
          ))}
        </MenuPaper>
      </Popper>
    </>
  );
};

DatasetDetailSort.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorEl: PropTypes.any,
  sortOrder: PropTypes.string,
  setSortOrder: PropTypes.func,
  sortKey: PropTypes.string,
  setSortKey: PropTypes.func,
  metricList: PropTypes.array,
};

export default DatasetDetailSort;
