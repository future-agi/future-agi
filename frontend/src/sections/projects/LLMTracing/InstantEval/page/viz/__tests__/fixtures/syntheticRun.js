// A synthetic Ask run shaped like useAskRun's result, for tests and previews:
// a week of support sessions with an order-system outage two days before
// `now`, 2–5 PM, where every session ended "No" and most of them hit an error,
// and one customer whose sessions all ended "No".

export const RESOLVED = "cfg-resolved";
export const TOPIC = "cfg-topic";

export const QUESTIONS = [
  {
    configId: RESOLVED,
    question: "Did the agent resolve the customer's issue?",
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
  {
    configId: TOPIC,
    question: "What did the customer want?",
    answerType: "category",
    labels: ["Order status", "Refund", "Product question", "Account change"],
  },
];

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

const OPENERS = {
  "Order status": "Where is my order HP-{n}? It was due yesterday.",
  Refund: "I'd like a refund for the jacket I returned last week.",
  "Product question": "Is the Coastline Rain Shell good for heavy rain?",
  "Account change": "I need to change the email on my account.",
};

export const OUTAGE_COUNT = 9;
export const OUTAGE_ERRORS = 6;
export const REPEAT_USER = "cust_noah_131";

/**
 * {records, questions, window, outage: {day, ids}}; `answered` is the share
 * of rows with answers (the rest are being read or not reached yet).
 */
export function syntheticRun({
  now = new Date(2026, 9, 15, 18, 0),
  seed = 7,
  answered = 1,
} = {}) {
  const random = seeded(seed);
  const pick = (list) => list[Math.floor(random() * list.length)];
  const users = Array.from(
    { length: 34 },
    (_, i) =>
      `cust_${["ava", "ben", "mia", "liam", "zoe", "omar", "ivy", "leo"][i % 8]}_${100 + i * 7}`,
  );
  const records = [];
  let n = 0;
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const outageDay = new Date(today);
  outageDay.setDate(outageDay.getDate() - 2);
  const add = ({
    time,
    user,
    resolved,
    topic,
    error = false,
    turns,
    model,
  }) => {
    n += 1;
    const opener = OPENERS[topic].replace("{n}", String(40000 + n * 37));
    records.push({
      id: `sess-${String(n).padStart(3, "0")}`,
      unit: "sessions",
      time: time.toISOString(),
      user_id: user,
      end_user_id: user,
      label: opener,
      excerpt: opener,
      trace_id: `trace-${n}`,
      facets: {
        model: model || (random() < 0.8 ? "gpt-4o-mini" : "claude-haiku-5-5"),
        turns: turns || 1 + Math.floor(random() * 5),
        duration_ms: Math.round(20000 + random() * 120000),
        error,
      },
      answers: {
        [RESOLVED]: {
          answer: resolved,
          confidence: Math.round((0.52 + random() * 0.46) * 100) / 100,
          explanation:
            resolved === "Yes"
              ? "The agent answered the question and the customer confirmed."
              : "The customer left without a resolution.",
          status: "completed",
        },
        [TOPIC]: {
          answer: topic,
          confidence: 0.9,
          explanation: `The customer asked about ${topic.toLowerCase()}.`,
          status: "completed",
        },
      },
    });
  };
  // Ordinary traffic: about 14 sessions a day, 8 AM to 10 PM.
  for (let d = 6; d >= 0; d -= 1) {
    const day = new Date(today);
    day.setDate(day.getDate() - d);
    const count = 12 + Math.floor(random() * 5);
    for (let i = 0; i < count; i += 1) {
      const hour = 8 + Math.floor(random() * 14);
      // Keep the outage stretch to the outage itself.
      if (d === 2 && hour >= 14 && hour < 17) continue;
      const time = new Date(
        day.getFullYear(),
        day.getMonth(),
        day.getDate(),
        hour,
        Math.floor(random() * 60),
      );
      if (time > now) continue;
      add({
        time,
        user: pick(users),
        resolved: random() < 0.55 ? "Yes" : "No",
        topic: pick(Object.keys(OPENERS)),
      });
    }
  }
  // The outage: order lookups failed and every session ended unresolved.
  const outageIds = [];
  for (let i = 0; i < OUTAGE_COUNT; i += 1) {
    const time = new Date(
      outageDay.getFullYear(),
      outageDay.getMonth(),
      outageDay.getDate(),
      14 + Math.floor((i * 3) / OUTAGE_COUNT),
      5 + i * 6,
    );
    add({
      time,
      user: pick(users),
      resolved: "No",
      topic: "Order status",
      error: i % 3 !== 2,
      turns: 4,
    });
    outageIds.push(records[records.length - 1].id);
  }
  // One customer who keeps coming back and never gets an answer.
  for (let i = 0; i < 10; i += 1) {
    const time = new Date(
      today.getFullYear(),
      today.getMonth(),
      today.getDate() - 6 + Math.floor(i / 2),
      9 + (i % 2) * 2,
      20,
    );
    add({ time, user: REPEAT_USER, resolved: "No", topic: "Account change" });
  }
  records.sort((a, b) => new Date(a.time) - new Date(b.time));
  // Part-way through a run: the newest rows are being read or not reached.
  if (answered < 1) {
    const cut = Math.round(records.length * answered);
    records.forEach((record, i) => {
      if (i < cut) return;
      if (i < cut + 6)
        record.answers = {
          [RESOLVED]: {
            answer: null,
            confidence: null,
            explanation: "",
            status: "running",
          },
        };
      else record.answers = {};
    });
  }
  const start = new Date(today);
  start.setDate(start.getDate() - 6);
  return {
    questions: QUESTIONS,
    records,
    window: [start.toISOString(), now.toISOString()],
    outage: { day: outageDay, ids: outageIds },
  };
}
