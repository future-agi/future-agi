import React from "react";
import SvgIcon from "@mui/material/SvgIcon";
/* eslint-disable react-refresh/only-export-components */

import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------

const ArrowDownIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M12,16 C11.7663478,16.0004565 11.5399121,15.9190812 11.36,15.77 L5.36,10.77 C4.93474074,10.4165378 4.87653776,9.78525926 5.23,9.36 C5.58346224,8.93474074 6.21474074,8.87653776 6.64,9.23 L12,13.71 L17.36,9.39 C17.5665934,9.2222295 17.8315409,9.14373108 18.0961825,9.17188444 C18.3608241,9.2000378 18.6033268,9.33252029 18.77,9.54 C18.9551341,9.74785947 19.0452548,10.0234772 19.0186853,10.3005589 C18.9921158,10.5776405 18.8512608,10.8311099 18.63,11 L12.63,15.83 C12.444916,15.955516 12.2231011,16.0153708 12,16 Z" />
  </SvgIcon>
);

const CheckboxIcon = (props) => (
  <SvgIcon {...props} viewBox="0 0 24 24">
    <rect
      x="3.25"
      y="3.25"
      width="17.5"
      height="17.5"
      rx="2"
      ry="2"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    />
  </SvgIcon>
);

const CheckboxCheckedIcon = (props) => (
  <SvgIcon {...props} viewBox="0 0 24 24">
    <rect
      x="3"
      y="3"
      width="18"
      height="18"
      rx="2"
      ry="2"
      fill="currentColor"
    />
    <path
      d="m8.25 12.25 2.5 2.5 5-5"
      fill="none"
      stroke="#fff"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </SvgIcon>
);

const CheckboxIndeterminateIcon = (props) => (
  <SvgIcon {...props} viewBox="0 0 24 24">
    <rect
      x="3"
      y="3"
      width="18"
      height="18"
      rx="2"
      ry="2"
      fill="currentColor"
    />
    <path
      d="M8 12h8"
      fill="none"
      stroke="#fff"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </SvgIcon>
);

const RadioIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M12 2A10 10 0 1 1 2 12C2 6.477 6.477 2 12 2Zm0 1.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17Z" />
  </SvgIcon>
);

const RadioCheckedIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M12 2A10 10 0 1 1 2 12C2 6.477 6.477 2 12 2Zm0 1.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10Z" />
  </SvgIcon>
);

const RatingIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M17.56,21 C17.4000767,21.0006435 17.2423316,20.9629218 17.1,20.89 L12,18.22 L6.9,20.89 C6.56213339,21.067663 6.15259539,21.0374771 5.8444287,20.8121966 C5.53626201,20.5869161 5.38323252,20.2058459 5.45,19.83 L6.45,14.2 L2.33,10.2 C2.06805623,9.93860108 1.9718844,9.55391377 2.08,9.2 C2.19824414,8.83742187 2.51242293,8.57366684 2.89,8.52 L8.59,7.69 L11.1,2.56 C11.2670864,2.21500967 11.6166774,1.99588989 12,1.99588989 C12.3833226,1.99588989 12.7329136,2.21500967 12.9,2.56 L15.44,7.68 L21.14,8.51 C21.5175771,8.56366684 21.8317559,8.82742187 21.95,9.19 C22.0581156,9.54391377 21.9619438,9.92860108 21.7,10.19 L17.58,14.19 L18.58,19.82 C18.652893,20.2027971 18.4967826,20.5930731 18.18,20.82 C17.9989179,20.9468967 17.7808835,21.010197 17.56,21 L17.56,21 Z" />
  </SvgIcon>
);

const TreeViewCollapseIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M18,3 C19.6568542,3 21,4.34314575 21,6 L21,6 L21,18 C21,19.6568542 19.6568542,21 18,21 L18,21 L6,21 C4.34314575,21 3,19.6568542 3,18 L3,18 L3,6 C3,4.34314575 4.34314575,3 6,3 L6,3 Z M18,5 L6,5 C5.44771525,5 5,5.44771525 5,6 L5,6 L5,18 C5,18.5522847 5.44771525,19 6,19 L6,19 L18,19 C18.5522847,19 19,18.5522847 19,18 L19,18 L19,6 C19,5.44771525 18.5522847,5 18,5 L18,5 Z M12,8 C12.5522847,8 13,8.44771525 13,9 L13,9 L13,11 L15,11 C15.5522847,11 16,11.4477153 16,12 C16,12.5522847 15.5522847,13 15,13 L15,13 L13,13 L13,15 C13,15.5522847 12.5522847,16 12,16 C11.4477153,16 11,15.5522847 11,15 L11,15 L11,13 L9,13 C8.44771525,13 8,12.5522847 8,12 C8,11.4477153 8.44771525,11 9,11 L9,11 L11,11 L11,9 C11,8.44771525 11.4477153,8 12,8 Z" />
  </SvgIcon>
);

const TreeViewExpandIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M18,3 C19.6568542,3 21,4.34314575 21,6 L21,6 L21,18 C21,19.6568542 19.6568542,21 18,21 L18,21 L6,21 C4.34314575,21 3,19.6568542 3,18 L3,18 L3,6 C3,4.34314575 4.34314575,3 6,3 L6,3 Z M18,5 L6,5 C5.44771525,5 5,5.44771525 5,6 L5,6 L5,18 C5,18.5522847 5.44771525,19 6,19 L6,19 L18,19 C18.5522847,19 19,18.5522847 19,18 L19,18 L19,6 C19,5.44771525 18.5522847,5 18,5 L18,5 Z M15,11 C15.5522847,11 16,11.4477153 16,12 C16,12.5522847 15.5522847,13 15,13 L15,13 L9,13 C8.44771525,13 8,12.5522847 8,12 C8,11.4477153 8.44771525,11 9,11 L9,11 Z" />
  </SvgIcon>
);

const TreeViewEndIcon = (props) => (
  <SvgIcon {...props}>
    <path d="M18,3 C19.6568542,3 21,4.34314575 21,6 L21,6 L21,18 C21,19.6568542 19.6568542,21 18,21 L18,21 L6,21 C4.34314575,21 3,19.6568542 3,18 L3,18 L3,6 C3,4.34314575 4.34314575,3 6,3 L6,3 Z M18,5 L6,5 C5.44771525,5 5,5.44771525 5,6 L5,6 L5,18 C5,18.5522847 5.44771525,19 6,19 L6,19 L18,19 C18.5522847,19 19,18.5522847 19,18 L19,18 L19,6 C19,5.44771525 18.5522847,5 18,5 L18,5 Z M14,8.99420168 C14.2666375,8.99420168 14.5222334,9.10068735 14.71,9.29 C14.8993127,9.4777666 15.0057983,9.73336246 15.0057983,10 C15.0057983,10.2666375 14.8993127,10.5222334 14.71,10.71 L14.71,10.71 L13.41,12 L14.71,13.29 C14.8993127,13.4777666 15.0057983,13.7333625 15.0057983,14 C15.0057983,14.2666375 14.8993127,14.5222334 14.71,14.71 C14.5222334,14.8993127 14.2666375,15.0057983 14,15.0057983 C13.7333625,15.0057983 13.4777666,14.8993127 13.29,14.71 L13.29,14.71 L12,13.41 L10.71,14.71 C10.5222334,14.8993127 10.2666375,15.0057983 10,15.0057983 C9.73336246,15.0057983 9.4777666,14.8993127 9.29,14.71 C9.10068735,14.5222334 8.99420168,14.2666375 8.99420168,14 C8.99420168,13.7333625 9.10068735,13.4777666 9.29,13.29 L9.29,13.29 L10.59,12 L9.29,10.71 C8.89787783,10.3178778 8.89787783,9.68212217 9.29,9.29 C9.68212217,8.89787783 10.3178778,8.89787783 10.71,9.29 L10.71,9.29 L12,10.59 L13.29,9.29 C13.4777666,9.10068735 13.7333625,8.99420168 14,8.99420168 Z" />
  </SvgIcon>
);

// ----------------------------------------------------------------------

export function defaultProps(theme) {
  return {
    MuiAlert: {
      defaultProps: {
        iconMapping: {
          error: <Iconify icon="solar:danger-bold" width={24} />,
          info: <Iconify icon="solar:info-circle-linear" width={24} />,
          success: <Iconify icon="solar:check-circle-bold" width={24} />,
          warning: <Iconify icon="solar:danger-triangle-linear" width={24} />,
        },
      },
    },
    MuiStack: {
      defaultProps: {
        useFlexGap: true,
      },
    },
    MuiAppBar: {
      defaultProps: {
        color: "transparent",
      },
    },
    MuiAvatarGroup: {
      defaultProps: {
        max: 4,
      },
    },
    MuiButtonGroup: {
      defaultProps: {
        disableElevation: true,
      },
    },
    MuiButton: {
      defaultProps: {
        color: "inherit",
        disableElevation: true,
      },
    },
    MuiCardHeader: {
      defaultProps: {
        titleTypographyProps: { variant: "h6" },
        subheaderTypographyProps: {
          variant: "body2",
          marginTop: theme.spacing(0.5),
        },
      },
    },
    MuiChip: {
      defaultProps: {
        deleteIcon: <Iconify icon="solar:close-circle-bold" />,
      },
    },
    MuiDialogActions: {
      defaultProps: {
        disableSpacing: true,
      },
    },
    MuiFab: {
      defaultProps: {
        color: "primary",
      },
    },
    MuiLink: {
      defaultProps: {
        underline: "hover",
      },
    },
    MuiListItemText: {
      defaultProps: {
        primaryTypographyProps: {
          typography: "subtitle2",
        },
        secondaryTypographyProps: {
          component: "span",
        },
      },
    },
    MuiPaper: {
      defaultProps: {
        elevation: 0,
      },
    },
    MuiSkeleton: {
      defaultProps: {
        animation: "wave",
        variant: "rounded",
      },
    },
    MuiFilledInput: {
      defaultProps: {
        disableUnderline: true,
      },
    },
    MuiFormHelperText: {
      defaultProps: {
        component: "div",
      },
    },
    MuiTab: {
      defaultProps: {
        disableRipple: true,
        iconPosition: "start",
      },
    },
    MuiTabs: {
      defaultProps: {
        textColor: "inherit",
        variant: "scrollable",
        allowScrollButtonsMobile: true,
      },
    },
    MuiTablePagination: {
      defaultProps: {
        backIconButtonProps: {
          size: "small",
        },
        nextIconButtonProps: {
          size: "small",
        },
      },
    },
    MuiSlider: {
      defaultProps: {
        size: "small",
      },
    },
    MuiAutocomplete: {
      defaultProps: {
        popupIcon: <ArrowDownIcon />,
      },
    },
    MuiSelect: {
      defaultProps: {
        IconComponent: ArrowDownIcon,
      },
    },
    MuiNativeSelect: {
      defaultProps: {
        IconComponent: ArrowDownIcon,
      },
    },
    MuiCheckbox: {
      defaultProps: {
        size: "small",
        icon: <CheckboxIcon />,
        checkedIcon: <CheckboxCheckedIcon />,
        indeterminateIcon: <CheckboxIndeterminateIcon />,
      },
    },
    MuiRadio: {
      defaultProps: {
        size: "small",
        icon: <RadioIcon />,
        checkedIcon: <RadioCheckedIcon />,
      },
    },
    MuiRating: {
      defaultProps: {
        emptyIcon: <RatingIcon />,
        icon: <RatingIcon />,
      },
    },
    MuiTreeView: {
      defaultProps: {
        defaultCollapseIcon: <TreeViewCollapseIcon />,
        defaultExpandIcon: <TreeViewExpandIcon />,
        defaultEndIcon: <TreeViewEndIcon />,
      },
    },
  };
}
