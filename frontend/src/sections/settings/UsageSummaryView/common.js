import _ from "lodash";
import { blue, green, orange, pink, red } from "../../../theme/palette";
import { alpha } from "@mui/material";
import { fCurrency } from "src/utils/format-number";

export const ICON_MAPPER = {
  credits_used: "/assets/icons/usage-summary/ic_credit_card.svg",
  traces: "/assets/icons/usage-summary/Ic_list_tree.svg",
  evaluations: "/assets/icons/ic_rounded_square.svg",
  error_localizations: "/assets/icons/usage-summary/ic_shield_alert.svg",
  agent_compass: "/assets/icons/navbar/ic_feed.svg",
  simulate: "/assets/icons/usage-summary/ic_simulate.svg",
};
export const ICON_STYLES = {
  credits_used: {
    iconStyle: {
      color: "green.500",
    },
    containerStyle: {
      "background-color": green.o5,
    },
  },
  traces: {
    iconStyle: {
      color: "orange.500",
    },
    containerStyle: {
      "background-color": orange.o5,
    },
  },
  evaluations: {
    iconStyle: {
      color: "blue.500",
    },
    containerStyle: {
      "background-color": blue.o5,
    },
  },
  error_localizations: {
    iconStyle: {
      color: "red.500",
    },
    containerStyle: {
      "background-color": red.o5,
    },
  },
  agent_compass: {
    iconStyle: {
      color: "primary.main",
    },
    containerStyle: {
      "background-color": (theme) => alpha(theme.palette.primary.main, 0.05),
    },
  },
  simulate: {
    iconStyle: {
      color: "pink.500",
    },
    containerStyle: {
      "background-color": pink.o5,
    },
  },
};

export const tabOptions = [
  { label: "Cost", value: "cost", disabled: false },
  { label: "Count", value: "count", disabled: false },
];

export const usageDefaultColDef = {
  lockVisible: true,
  sortable: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

export const getBreakdownColumnDefs = (currentTab) => {
  return [
    {
      headerName: "Sections",
      field: "name",
      flex: 3,
      columnId: "name",
      valueFormatter: (params) => {
        if (!params.node.rowPinned && typeof params.value === "string") {
          return _.startCase(params.value.replace(/_/g, " "));
        }
        return params.value;
      },
    },
    {
      headerName:
        currentTab === "cost" ? "Evaluations Cost" : "Evaluations Count",
      field: currentTab === "cost" ? "cost" : "count",
      flex: 1,
      columnId: currentTab === "cost" ? "cost" : "count",
      valueFormatter: (params) => {
        if (currentTab === "cost") {
          return fCurrency(params.value, true);
        } else {
          return undefined;
        }
      },
    },
  ];
};

const titlesMap = {
  credits_used: "Credits used",
  traces: "Cost of traces (Count)",
  evaluations: "Cost of evaluations (Count)",
  error_localizations: "Cost of error localization (Count)",
  agent_compass: "Cost of Error Feed (Count)",
  simulate: "Simulate (Count)",
};

export function convertTotalsToMetrics(totals) {
  if (!totals || typeof totals !== "object") {
    return []; // safe fallback
  }

  return Object.entries(totals).map(([key, data]) => {
    if (!data || typeof data !== "object") {
      data = {}; // fallback for missing data
    }

    // Keep null if it's null, otherwise default to "0%"
    const percentageChange =
      data.percentage_change === null || data.percentage_change === undefined
        ? null
        : String(data.percentage_change);

    const changeType =
      percentageChange && percentageChange.startsWith("-")
        ? "negative"
        : "positive";

    let value = typeof data.cost === "number" ? data.cost : 0;

    // Optionally replace credits_used value with total_credit if available
    if (key === "credits_used" && typeof data.total_credit === "number") {
      value = data.total_credit;
    }

    let formattedValue = "$0";
    try {
      formattedValue = fCurrency(value, true) || "$0";
    } catch (e) {
      formattedValue = "$0";
    }

    return {
      id: key,
      title: (titlesMap && titlesMap[key]) || key,
      value: formattedValue,
      subtitle: data.count != null ? `(${data.count})` : "",
      percentageChange,
      changeLabel:
        key === "credits_used" ? "used from last month" : "from last month",
      changeType,
    };
  });
}

/**
 * Convert a single workspace row from /workspace-usage-summary/ into metric cards.
 * The workspace row has shape: { overall, traces, evaluations, errorLocalizations, agentCompass, simulate }
 * Each sub-object has { cost, count }.
 */
export function convertWorkspaceToMetrics(ws) {
  if (!ws || typeof ws !== "object") return [];

  const mapping = [
    { key: "credits_used", data: ws.overall },
    { key: "traces", data: ws.traces },
    { key: "evaluations", data: ws.evaluations },
    { key: "error_localizations", data: ws.error_localizations },
    { key: "agent_compass", data: ws.agent_compass },
    { key: "simulate", data: ws.simulate },
  ];

  return mapping.map(({ key, data }) => {
    const d = data || {};
    const value = typeof d.cost === "number" ? d.cost : 0;
    let formattedValue = "$0";
    try {
      formattedValue = fCurrency(value, true) || "$0";
    } catch (e) {
      formattedValue = "$0";
    }

    return {
      id: key,
      title: (titlesMap && titlesMap[key]) || key,
      value: formattedValue,
      subtitle: d.count != null ? `(${d.count})` : "",
      percentageChange: null,
      changeLabel:
        key === "credits_used" ? "used from last month" : "from last month",
      changeType: "positive",
    };
  });
}

const numericMonths = {
  january: 1,
  february: 2,
  march: 3,
  april: 4,
  may: 5,
  june: 6,
  july: 7,
  august: 8,
  september: 9,
  october: 10,
  november: 11,
  december: 12,
};
export function getMonthAndYear(input) {
  const [monthName, yearStr] = input.split("_");
  const year = parseInt(yearStr, 10);

  const month = numericMonths[monthName.toLowerCase()];
  if (!month) throw new Error("Invalid month name");

  return { month, year };
}

const months = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];

export function generateMonthsUpToCurrent(input) {
  const [monthName, yearStr] = input.split("_");
  const year = parseInt(yearStr, 10);

  const startMonthIndex = months.indexOf(monthName.toLowerCase());
  if (startMonthIndex === -1) throw new Error("Invalid month name");

  const currentDate = new Date();
  const currentMonthIndex = currentDate.getMonth();
  const currentYear = currentDate.getFullYear();

  const result = [];
  let y = year;
  let m = startMonthIndex;

  // Loop forward till current month/year
  while (y < currentYear || (y === currentYear && m <= currentMonthIndex)) {
    result.unshift({ value: `${months[m]}_${y}` }); // reverse order
    m++;
    if (m > 11) {
      m = 0;
      y++;
    }
  }

  return result;
}

export function getLastThirteenMonths() {
  const currentDate = new Date();
  let month = currentDate.getMonth(); // 0–11
  let year = currentDate.getFullYear();

  const result = [];

  // Loop 13 times (current month + previous 12)
  for (let i = 0; i < 13; i++) {
    result.push({
      value: `${months[month]}_${year}`,
    });

    // Move one month backward
    month--;
    if (month < 0) {
      month = 11; // December
      year--;
    }
  }

  return result;
}
