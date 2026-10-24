import React from "react";
import { useState, useCallback } from "react";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Container from "@mui/material/Container";
import Iconify from "src/components/iconify";
import { useSettingsContext } from "src/components/settings";

import AccountGeneral from "../account-general";
import AccountChangePassword from "../account-change-password";
import UserListPage from "src/pages/dashboard/user/list";
import { Typography } from "@mui/material";

// ----------------------------------------------------------------------

const TABS = [
  {
    value: "overview",
    label: "Overview",
    icon: <Iconify icon="solar:user-id-bold" width={24} />,
  },
  {
    value: "users",
    label: "Users",
    icon: <Iconify icon="solar:key-linear" width={24} />,
  },
  {
    value: "security",
    label: "Security",
    icon: <Iconify icon="solar:key-linear" width={24} />,
  },
];

// ----------------------------------------------------------------------

export default function AccountView() {
  const settings = useSettingsContext();

  const [currentTab, setCurrentTab] = useState("general");

  const handleChangeTab = useCallback((event, newValue) => {
    setCurrentTab(newValue);
  }, []);

  return (
    <Container maxWidth={settings.themeStretch ? false : "lg"}>
      <Typography variant="h2"> Settings </Typography>
      <Tabs
        value={currentTab}
        onChange={handleChangeTab}
        sx={{
          mb: { xs: 3, md: 3 },
        }}
      >
        {TABS.map((tab) => (
          <Tab
            key={tab.value}
            label={tab.label}
            icon={tab.icon}
            value={tab.value}
          />
        ))}
      </Tabs>

      {currentTab === "overview" && <AccountGeneral />}

      {currentTab === "users" && <UserListPage />}

      {currentTab === "security" && <AccountChangePassword />}
    </Container>
  );
}
