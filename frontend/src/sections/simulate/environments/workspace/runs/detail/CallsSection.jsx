import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { Count, GridTable, Section } from "./trialChangesParts";
import {
  TRIAL_CHANGES_COPY as C,
  OPTIONAL_COUNTS,
} from "./trialChanges.constants";

import { ParticipantActions } from "./ParticipantActions";

export function CallsSection({ changes }) {
  const extra = OPTIONAL_COUNTS.filter(({ key }) =>
    changes.some((one) => one[key]),
  );
  const cols = `minmax(120px, 1.4fr) repeat(${2 + extra.length}, minmax(64px, 0.6fr))`;
  const H = C.callsHead;
  const head = [
    H.who,
    { label: H.attempted, align: "right" },
    { label: H.committed, align: "right" },
    ...extra.map(({ key }) => ({ label: H[key], align: "right" })),
  ];
  return (
    <Section title={C.callsHeading} hint={C.about}>
      <GridTable
        cols={cols}
        head={head}
        rows={changes.map((one) => ({
          key: one.who,
          label: C.counts(one),
          cells: [
            <Typography
              key="n"
              sx={{
                typography: "s2",
                fontWeight: "fontWeightMedium",
                wordBreak: "break-word",
              }}
            >
              {one.name}
            </Typography>,
            <Count key="a" value={one.attempted} />,
            <Count key="c" value={one.committed} tone="text.primary" />,
            ...extra.map(({ key, tone }) => (
              <Count key={key} value={one[key]} tone={tone} hideZero />
            )),
          ],
          below: <ParticipantActions one={one} />,
        }))}
      />
    </Section>
  );
}
CallsSection.propTypes = { changes: PropTypes.array.isRequired };
