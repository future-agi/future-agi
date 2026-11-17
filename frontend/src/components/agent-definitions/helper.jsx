import { Box } from "@mui/material";
// import { AgentActionMenu } from "./AgentActionMenu";
import AgentNameCellRenderer from "./CustomCellRenderers/AgentNameCellRenderer";
import ChipCellRenderer from "src/components/scenarios/CustomCellRenderers/ChipCellRenderer";
// import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { camelCaseToTitleCase } from "src/utils/utils";
import { AGENT_TYPES } from "src/sections/agents/constants";

export const languageMap = {
  ar: "Arabic",
  bg: "Bulgarian",
  zh: "Chinese",
  cs: "Czech",
  da: "Danish",
  nl: "Dutch",
  en: "English",
  fi: "Finnish",
  fr: "French",
  de: "German",
  el: "Greek",
  hi: "Hindi",
  hu: "Hungarian",
  id: "Indonesian",
  it: "Italian",
  ja: "Japanese",
  ko: "Korean",
  ms: "Malay",
  no: "Norwegian",
  pl: "Polish",
  pt: "Portuguese",
  ro: "Romanian",
  ru: "Russian",
  sk: "Slovak",
  es: "Spanish",
  sv: "Swedish",
  tr: "Turkish",
  uk: "Ukrainian",
  vi: "Vietnamese",
};

export const languageOptions = [
  { value: "ar", label: "Arabic" },
  { value: "bg", label: "Bulgarian" },
  { value: "zh", label: "Chinese Simplified" },
  { value: "cs", label: "Czech" },
  { value: "da", label: "Danish" },
  { value: "nl", label: "Dutch" },
  { value: "en", label: "English" },
  { value: "fi", label: "Finnish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "el", label: "Greek" },
  { value: "hi", label: "Hindi" },
  { value: "hu", label: "Hungarian" },
  { value: "id", label: "Indonesian" },
  { value: "it", label: "Italian" },
  { value: "ja", label: "Japanese" },
  { value: "ko", label: "Korean" },
  { value: "ms", label: "Malay" },
  { value: "no", label: "Norwegian" },
  { value: "pl", label: "Polish" },
  { value: "pt", label: "Portuguese" },
  { value: "ro", label: "Romanian" },
  { value: "ru", label: "Russian" },
  { value: "sk", label: "Slovak" },
  { value: "es", label: "Spanish" },
  { value: "sv", label: "Swedish" },
  { value: "tr", label: "Turkish" },
  { value: "uk", label: "Ukrainian" },
  { value: "vi", label: "Vietnamese" },
];

export const pinCodeOptions = [
  {
    label: "United States",
    value: "1",
    countryFlag: "/assets/country-flags/us.svg",
  },
  { label: "Canada", value: "1", countryFlag: "/assets/country-flags/ca.svg" },
  {
    label: "United Kingdom",
    value: "44",
    countryFlag: "/assets/country-flags/gb.svg",
  },
  { label: "India", value: "91", countryFlag: "/assets/country-flags/in.svg" },
  {
    label: "Australia",
    value: "61",
    countryFlag: "/assets/country-flags/au.svg",
  },
  {
    label: "Germany",
    value: "49",
    countryFlag: "/assets/country-flags/de.svg",
  },
  { label: "France", value: "33", countryFlag: "/assets/country-flags/fr.svg" },
  { label: "Italy", value: "39", countryFlag: "/assets/country-flags/it.svg" },
  { label: "Spain", value: "34", countryFlag: "/assets/country-flags/es.svg" },
  {
    label: "Netherlands",
    value: "31",
    countryFlag: "/assets/country-flags/nl.svg",
  },
  { label: "Sweden", value: "46", countryFlag: "/assets/country-flags/se.svg" },
  { label: "Norway", value: "47", countryFlag: "/assets/country-flags/no.svg" },
  {
    label: "Denmark",
    value: "45",
    countryFlag: "/assets/country-flags/dk.svg",
  },
  {
    label: "Switzerland",
    value: "41",
    countryFlag: "/assets/country-flags/ch.svg",
  },
  {
    label: "Belgium",
    value: "32",
    countryFlag: "/assets/country-flags/be.svg",
  },
  {
    label: "Austria",
    value: "43",
    countryFlag: "/assets/country-flags/at.svg",
  },
  {
    label: "Finland",
    value: "358",
    countryFlag: "/assets/country-flags/fi.svg",
  },
  {
    label: "Ireland",
    value: "353",
    countryFlag: "/assets/country-flags/ie.svg",
  },
  {
    label: "Portugal",
    value: "351",
    countryFlag: "/assets/country-flags/pt.svg",
  },
  { label: "Greece", value: "30", countryFlag: "/assets/country-flags/gr.svg" },
  { label: "Poland", value: "48", countryFlag: "/assets/country-flags/pl.svg" },
  {
    label: "Czech Republic",
    value: "420",
    countryFlag: "/assets/country-flags/cz.svg",
  },
  {
    label: "Hungary",
    value: "36",
    countryFlag: "/assets/country-flags/hu.svg",
  },
  {
    label: "Romania",
    value: "40",
    countryFlag: "/assets/country-flags/ro.svg",
  },
  { label: "Russia", value: "7", countryFlag: "/assets/country-flags/ru.svg" },
  {
    label: "Ukraine",
    value: "380",
    countryFlag: "/assets/country-flags/ua.svg",
  },
  { label: "Turkey", value: "90", countryFlag: "/assets/country-flags/tr.svg" },
  {
    label: "Saudi Arabia",
    value: "966",
    countryFlag: "/assets/country-flags/sa.svg",
  },
  {
    label: "United Arab Emirates",
    value: "971",
    countryFlag: "/assets/country-flags/ae.svg",
  },
  { label: "Qatar", value: "974", countryFlag: "/assets/country-flags/qa.svg" },
  {
    label: "Kuwait",
    value: "965",
    countryFlag: "/assets/country-flags/kw.svg",
  },
  { label: "Oman", value: "968", countryFlag: "/assets/country-flags/om.svg" },
  {
    label: "Bahrain",
    value: "973",
    countryFlag: "/assets/country-flags/bh.svg",
  },
  {
    label: "Pakistan",
    value: "92",
    countryFlag: "/assets/country-flags/pk.svg",
  },
  {
    label: "Bangladesh",
    value: "880",
    countryFlag: "/assets/country-flags/bd.svg",
  },
  {
    label: "Sri Lanka",
    value: "94",
    countryFlag: "/assets/country-flags/lk.svg",
  },
  { label: "Nepal", value: "977", countryFlag: "/assets/country-flags/np.svg" },
  {
    label: "Bhutan",
    value: "975",
    countryFlag: "/assets/country-flags/bt.svg",
  },
  { label: "China", value: "86", countryFlag: "/assets/country-flags/cn.svg" },
  { label: "Japan", value: "81", countryFlag: "/assets/country-flags/jp.svg" },
  {
    label: "South Korea",
    value: "82",
    countryFlag: "/assets/country-flags/kr.svg",
  },
  {
    label: "Taiwan",
    value: "886",
    countryFlag: "/assets/country-flags/tw.svg",
  },
  {
    label: "Hong Kong",
    value: "852",
    countryFlag: "/assets/country-flags/hk.svg",
  },
  {
    label: "Singapore",
    value: "65",
    countryFlag: "/assets/country-flags/sg.svg",
  },
  {
    label: "Malaysia",
    value: "60",
    countryFlag: "/assets/country-flags/my.svg",
  },
  {
    label: "Indonesia",
    value: "62",
    countryFlag: "/assets/country-flags/id.svg",
  },
  {
    label: "Thailand",
    value: "66",
    countryFlag: "/assets/country-flags/th.svg",
  },
  {
    label: "Vietnam",
    value: "84",
    countryFlag: "/assets/country-flags/vn.svg",
  },
  {
    label: "Philippines",
    value: "63",
    countryFlag: "/assets/country-flags/ph.svg",
  },
  {
    label: "New Zealand",
    value: "64",
    countryFlag: "/assets/country-flags/nz.svg",
  },
  {
    label: "South Africa",
    value: "27",
    countryFlag: "/assets/country-flags/za.svg",
  },
  {
    label: "Nigeria",
    value: "234",
    countryFlag: "/assets/country-flags/ng.svg",
  },
  { label: "Kenya", value: "254", countryFlag: "/assets/country-flags/ke.svg" },
  { label: "Egypt", value: "20", countryFlag: "/assets/country-flags/eg.svg" },
  {
    label: "Morocco",
    value: "212",
    countryFlag: "/assets/country-flags/ma.svg",
  },
  { label: "Brazil", value: "55", countryFlag: "/assets/country-flags/br.svg" },
  { label: "Mexico", value: "52", countryFlag: "/assets/country-flags/mx.svg" },
  {
    label: "Argentina",
    value: "54",
    countryFlag: "/assets/country-flags/ar.svg",
  },
  { label: "Chile", value: "56", countryFlag: "/assets/country-flags/cl.svg" },
  {
    label: "Colombia",
    value: "57",
    countryFlag: "/assets/country-flags/co.svg",
  },
  { label: "Peru", value: "51", countryFlag: "/assets/country-flags/pe.svg" },
  {
    label: "Venezuela",
    value: "58",
    countryFlag: "/assets/country-flags/ve.svg",
  },
];

export const getAgentColumnDefs = (_role) => [
  {
    headerName: "Agent Name",
    field: "agent_name",
    minWidth: 300,
    flex: 2,
    cellRenderer: AgentNameCellRenderer,
  },
  {
    headerName: "Type",
    field: "inbound",
    flex: 0.7,
    cellRenderer: ChipCellRenderer,
    cellRendererParams: {
      resolveKey: (data) =>
        data.agentType === AGENT_TYPES.VOICE
          ? data.inbound
            ? "voice_inbound"
            : "voice_outbound"
          : "chat",
    },
  },
  {
    headerName: "Provider",
    field: "provider",
    flex: 0.7,
    cellRenderer: ({ value }) => (
      <Box height={"100%"} display={"flex"} alignItems={"center"}>
        {value ? camelCaseToTitleCase(value) : "-"}
      </Box>
    ),
  },
  {
    headerName: "Contact Number",
    field: "contact_number",
    flex: 1,
    cellRenderer: (params) => {
      const { value, data } = params;
      return (
        <Box height="100%" display="flex" alignItems="center">
          {data?.agentType === AGENT_TYPES.CHAT ? "NA" : value}
        </Box>
      );
    },
  },

  {
    headerName: "Languages",
    field: "languages",
    flex: 0.7,
    cellRenderer: ({ value }) => {
      if (!value || !Array.isArray(value) || value.length === 0) return null;

      const maxVisible = 2;
      const visibleLanguages = value
        .slice(0, maxVisible)
        .map((lang) => languageMap[lang] || lang);
      const remainingCount = value.length - maxVisible;

      return (
        <Box
          sx={{
            display: "flex",
            height: "100%",
            alignItems: "center",
          }}
        >
          {visibleLanguages.join(", ")}
          {remainingCount > 0 && `, +${remainingCount}`}
        </Box>
      );
    },
  },
  {
    headerName: "Version",
    field: "latest_version",
    flex: 0.7,
    cellRenderer: ({ value }) => (
      <Box height={"100%"} display={"flex"} alignItems={"center"}>
        {value ? `v${value}` : "-"}
      </Box>
    ),
  },
  // ...(RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role] &&
  // RolePermission.SIMULATION_AGENT[PERMISSIONS.DELETE][role]
  //   ? [
  //       {
  //         headerName: "",
  //         field: "actions",
  //         maxWidth: 75,
  //         cellRenderer: (params) => (
  //           <Box
  //             height={"100%"}
  //             display={"flex"}
  //             alignItems={"center"}
  //             justifyContent={"center"}
  //           >
  //             <AgentActionMenu agent={params.data} onDelete={() => {}} />
  //           </Box>
  //         ),
  //       },
  //     ]
  //   : []),
];
