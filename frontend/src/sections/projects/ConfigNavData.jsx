/* eslint-disable react-refresh/only-export-components */
import React from "react";
import { useMemo } from "react";
import SvgColor from "src/components/svg-color";
import { useLocation } from "react-router";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

const icon = (name) => (
  <SvgColor
    src={`/assets/icons/navbar/${name}.svg`}
    sx={{ width: "20px", height: "20px" }}
  />
  // OR
  // <Iconify icon="fluent:mail-24-filled" />
  // https://icon-sets.iconify.design/solar/
  // https://www.streamlinehq.com/icons
);

const ICONS = {
  llmTracing: icon("ic_llm"),
  sessions: icon("ic_sessions"),
  embeddings: icon("ic_embeddings"),
  users: icon("ic_users"),
  dashboards: icon("ic_chartsObserve"),
  // projectConfig: icon("ic_projectconfig"),
};

// ----------------------------------------------------------------------

export function useNavData() {
  const { pathname } = useLocation();

  const base = pathname.split("/").slice(0, -1).join("/");

  const data = useMemo(
    () => [
      {
        subheader: "Observe",
        items: [
          {
            title: "LLM Tracing",
            icon: ICONS.llmTracing,
            path: `${base}/llm-tracing`,
            eventTrigger: () => {
              trackEvent(Events.pObserveShelfSelection, {
                [PropertyName.click]: "LLM Tracing",
              });
            },
          },
          {
            title: "Sessions",
            path: `${base}/sessions`,
            icon: ICONS.sessions,
            eventTrigger: () => {
              trackEvent(Events.pObserveShelfSelection, {
                [PropertyName.click]: "Sessions",
              });
            },
          },
          // {
          //   title: "Embeddings",
          //   path: "",
          //   icon: ICONS.embeddings,
          // },
        ],
      },
      {
        subheader: "Analysis",
        items: [
          {
            title: "Charts",
            path: `${base}/charts`,
            icon: ICONS.dashboards,
            eventTrigger: () => {
              trackEvent(Events.pObserveShelfSelection, {
                [PropertyName.click]: "Dashboards",
              });
            },
          },
          {
            title: "Users",
            path: `${base}/users`,
            icon: ICONS.users,
            eventTrigger: () => {
              trackEvent(Events.pObserveShelfSelection, {
                [PropertyName.click]: "Users",
              });
            },
          },
        ],
      },
      // {
      //   subheader: "Configure",
      //   items: [
      //     {
      //       title: "Project Settings",
      //       path: `${base}/project-settings`,
      //       icon: ICONS.projectConfig,
      //       eventTrigger: () => {
      //         trackEvent(Events.pObserveShelfSelection, {
      //           [PropertyName.click]: "Project Settings",
      //         });
      //       },
      //     },
      //   ],
      // },
    ],
    [base],
  );

  return data;
}
