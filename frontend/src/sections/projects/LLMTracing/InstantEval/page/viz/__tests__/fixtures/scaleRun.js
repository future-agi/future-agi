import { summarizeRecords } from "../../askSummary";

// Synthetic Ask runs with a Yes/No question and a 6-label category question,
// at two sizes: a week of 108 support sessions (rows and all), and a month of
// 100,000 (counts only, as the summary endpoint returns them). Both carry the
// same planted patterns:
//   - an outage on day −2 (−9 at scale), 2–5 PM: errors, and "No";
//   - returns and refunds twice as common at weekends;
//   - account or email changes resolved far less often;
//   - gpt-4.1-mini resolving less after a deploy on day −5 (scale only);
//   - a cohort of customers who never get an answer.

export const RESOLVED = "cfg-resolved";
export const PURPOSE = "cfg-purpose";
export const PURPOSE_LABELS = [
  "Order status",
  "Return or refund",
  "Product advice",
  "Account or email change",
  "Wholesale or pricing",
  "Something else",
];
export const QUESTIONS = [
  {
    configId: RESOLVED,
    question: "Did the agent resolve the customer's issue?",
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
  {
    configId: PURPOSE,
    question: "What did the customer come for?",
    answerType: "category",
    labels: PURPOSE_LABELS,
  },
];

export const NOW = new Date(2026, 9, 16, 18, 0);

// mulberry32: a small seeded generator so every run is the same.
function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const PURPOSE_WEEKDAY = [0.3, 0.15, 0.2, 0.11, 0.09, 0.15];
const PURPOSE_WEEKEND = [0.24, 0.36, 0.16, 0.08, 0.04, 0.12];
// Traffic by hour, quiet overnight.
const HOUR_WEIGHT = [
  1, 0.6, 0.4, 0.3, 0.3, 0.5, 1, 2, 4, 6, 7, 7, 6, 6, 7, 7, 6, 6, 5, 5, 4, 3, 2,
  1.5,
];
const NAMES = [
  "ava",
  "ben",
  "mia",
  "liam",
  "zoe",
  "omar",
  "ivy",
  "leo",
  "noah",
];
const OPENERS = [
  "Where is my order HP-{n}? It was due yesterday.",
  "I'd like to send back the boots I bought, they're too small.",
  "Is the Coastline Rain Shell good for heavy rain?",
  "I need to change the email on my account.",
  "Do you offer trade pricing for 200 jackets?",
  "Can I talk to someone about a gift card?",
];

const pickWeighted = (random, weights) => {
  const total = weights.reduce((s, w) => s + w, 0);
  let r = random() * total;
  for (let i = 0; i < weights.length; i += 1) {
    r -= weights[i];
    if (r <= 0) return i;
  }
  return weights.length - 1;
};

/**
 * Rows over `days` days up to `now`, answered or not:
 *   {records, window, outage: {day, ids}, cohort: [user ids]}
 * `answered` is the share answered so far, in a random (hash-like) order as
 * a sampled run fills in; the rest are being read or not reached.
 */
export function scaleRows({
  count = 108,
  days = 7,
  now = NOW,
  seed = 11,
  answered = 1,
  outageDaysAgo = 2,
  outageCount = 9,
  cohortSize = 1,
  cohortRows = 10,
  regression = false,
} = {}) {
  const random = seeded(seed);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const userCount = Math.max(34, Math.round(count / 10));
  const users = Array.from({ length: userCount }, (_, i) => ({
    id: `cust_${NAMES[i % NAMES.length]}_${100 + i}`,
    end: `0d6c${String(i).padStart(4, "0")}-0000-4000-8000-000000000000`,
  }));
  const cohort = users.slice(0, cohortSize);
  const outageDay = new Date(today);
  outageDay.setDate(outageDay.getDate() - outageDaysAgo);
  const deploy = new Date(today);
  deploy.setDate(deploy.getDate() - 5);
  const records = [];
  const make = ({ time, user, purpose, resolved, error = false, model }) => {
    const n = records.length + 1;
    const opener = OPENERS[purpose].replace("{n}", String(40000 + n * 37));
    const unsure = random() < 0.12;
    const failed = random() < 0.006;
    records.push({
      id: `sess-${String(n).padStart(6, "0")}`,
      unit: "sessions",
      time: time.toISOString(),
      user_id: user.id,
      end_user_id: user.end,
      label: opener,
      excerpt: opener,
      trace_id: null,
      facets: {
        model,
        turns: 1 + Math.floor(random() * 8),
        duration_ms: Math.round(20000 + random() * 120000),
        error,
      },
      answers: {
        [RESOLVED]: failed
          ? { answer: "__error__", status: "errored", confidence: null }
          : {
              answer: resolved ? "Yes" : "No",
              confidence: unsure ? 0.5 : 0.9,
              explanation: resolved
                ? "The agent answered and the customer confirmed."
                : "The customer left without a resolution.",
              status: "completed",
            },
        [PURPOSE]: {
          answer: PURPOSE_LABELS[purpose],
          confidence: unsure ? 0.55 : 0.92,
          explanation: `The customer came about ${PURPOSE_LABELS[purpose].toLowerCase()}.`,
          status: "completed",
        },
      },
    });
    return records[records.length - 1];
  };
  const modelOf = () => {
    const r = random();
    return r < 0.4 ? "gpt-4.1-mini" : r < 0.7 ? "gpt-4.1" : "claude-haiku-5-5";
  };
  const ordinary = count - outageCount - cohortSize * cohortRows;
  for (let i = 0; i < ordinary; i += 1) {
    const d = Math.floor(random() * days);
    const day = new Date(today);
    day.setDate(day.getDate() - d);
    const hour = pickWeighted(random, HOUR_WEIGHT);
    // Keep the outage stretch to the outage itself.
    if (d === outageDaysAgo && hour >= 14 && hour < 17) {
      i -= 1;
      continue;
    }
    const time = new Date(
      day.getFullYear(),
      day.getMonth(),
      day.getDate(),
      hour,
      Math.floor(random() * 60),
    );
    if (time > now) {
      i -= 1;
      continue;
    }
    const weekend = time.getDay() === 0 || time.getDay() === 6;
    const purpose = pickWeighted(
      random,
      weekend ? PURPOSE_WEEKEND : PURPOSE_WEEKDAY,
    );
    const model = modelOf();
    let yes = purpose === 3 ? 0.2 : 0.62;
    if (regression && model === "gpt-4.1-mini" && time >= deploy) yes = 0.28;
    make({
      time,
      user: users[cohortSize + Math.floor(random() * (userCount - cohortSize))],
      purpose,
      resolved: random() < yes,
      error: random() < 0.01,
      model,
    });
  }
  const outageIds = [];
  for (let i = 0; i < outageCount; i += 1) {
    const time = new Date(
      outageDay.getFullYear(),
      outageDay.getMonth(),
      outageDay.getDate(),
      14 + Math.floor((i * 3) / outageCount),
      Math.floor(((i * 60 * 3) / outageCount) % 60),
    );
    outageIds.push(
      make({
        time,
        user: users[cohortSize + (i % (userCount - cohortSize))],
        purpose: 0,
        resolved: random() < 0.05,
        error: random() < 0.75,
        model: modelOf(),
      }).id,
    );
  }
  cohort.forEach((user) => {
    for (let i = 0; i < cohortRows; i += 1) {
      const d = Math.floor(random() * days);
      const time = new Date(
        today.getFullYear(),
        today.getMonth(),
        today.getDate() - d,
        9 + Math.floor(random() * 10),
        Math.floor(random() * 60),
      );
      make({
        time: time > now ? new Date(now.getTime() - 3600e3) : time,
        user,
        purpose: 3,
        resolved: false,
        model: modelOf(),
      });
    }
  });
  records.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  // Part-way through: a hash-ordered sample fills in at random, not in time
  // order; a few rows are being read, the rest not reached.
  if (answered < 1) {
    const order = records
      .map((r, i) => [random(), i])
      .sort((a, b) => a[0] - b[0]);
    const cut = Math.round(records.length * answered);
    order.forEach(([, index], rank) => {
      if (rank < cut) return;
      const record = records[index];
      record.answers =
        rank < cut + Math.max(4, Math.round(records.length * 0.01))
          ? {
              [RESOLVED]: { answer: null, status: "running" },
              [PURPOSE]: { answer: null, status: "running" },
            }
          : {};
    });
  }
  const start = new Date(today);
  start.setDate(start.getDate() - (days - 1));
  return {
    questions: QUESTIONS,
    records,
    window: [start.toISOString(), now.toISOString()],
    outage: { day: outageDay, ids: outageIds },
    cohort: cohort.map((u) => u.id),
  };
}

/** The week of 108 sessions, rows and all. */
export const smallRun = (options = {}) =>
  scaleRows({ count: 108, days: 7, ...options });

/**
 * A month of 100,000 sessions as the summary endpoint returns them (counts
 * only). `sample` makes it a uniform sample of that many rows of a larger
 * view (`population`).
 */
export function bigSummary({
  answered = 1,
  count = 100000,
  population,
  now = NOW,
  seed = 23,
} = {}) {
  const run = scaleRows({
    count,
    days: 30,
    now,
    seed,
    answered,
    outageDaysAgo: 9,
    outageCount: Math.round(count * 0.004),
    cohortSize: 40,
    cohortRows: Math.max(4, Math.round(count / 4000)),
    regression: true,
  });
  const summary = summarizeRecords({
    records: run.records,
    questions: QUESTIONS,
    window: run.window,
    now: now.getTime(),
    unit: "sessions",
  });
  if (population && population > count)
    summary.sample = {
      population,
      population_is_lower_bound: false,
      requested: count,
      read: count,
      is_sample: true,
      can_extend: true,
      next_size: 1000,
    };
  summary.window = { from: run.window[0], to: run.window[1] };
  return { summary, run };
}
