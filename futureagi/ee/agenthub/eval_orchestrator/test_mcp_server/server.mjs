import express from "express";
import { randomUUID } from "crypto";

const app = express();
app.use(express.json());

// ── Helpers ──────────────────────────────────────────────────────────

function wordOverlap(textA, textB) {
  const wordsA = new Set(textA.toLowerCase().replace(/[^\w\s]/g, "").split(/\s+/).filter(Boolean));
  const wordsB = new Set(textB.toLowerCase().replace(/[^\w\s]/g, "").split(/\s+/).filter(Boolean));
  const intersection = [...wordsA].filter(w => wordsB.has(w));
  return intersection.length / Math.max(wordsA.size, 1);
}

function sentenceCount(text) {
  return (text.match(/[.!?]+/g) || []).length || 1;
}

function wordCount(text) {
  return text.split(/\s+/).filter(Boolean).length;
}

function syllableCount(word) {
  word = word.toLowerCase().replace(/[^a-z]/g, "");
  if (word.length <= 3) return 1;
  word = word.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, "");
  word = word.replace(/^y/, "");
  const m = word.match(/[aeiouy]{1,2}/g);
  return m ? m.length : 1;
}

function fleschKincaid(text) {
  const words = text.split(/\s+/).filter(Boolean);
  const wc = words.length || 1;
  const sc = sentenceCount(text);
  const totalSyllables = words.reduce((s, w) => s + syllableCount(w), 0);
  return 206.835 - 1.015 * (wc / sc) - 84.6 * (totalSyllables / wc);
}

// Simple rule-based grammar checks
const GRAMMAR_RULES = [
  { pattern: /\bi\b(?![''])/g, message: "Capitalize 'I' when used as a pronoun", severity: "error" },
  { pattern: /\s{2,}/g, message: "Multiple consecutive spaces", severity: "warning" },
  { pattern: /[.!?]\s*[a-z]/g, message: "Sentence should start with a capital letter", severity: "error" },
  { pattern: /\b(\w+)\s+\1\b/gi, message: "Repeated word detected", severity: "warning" },
  { pattern: /,\s*(and|or|but)\s*,/gi, message: "Unnecessary comma before conjunction", severity: "warning" },
];

// ── Tool Definitions ─────────────────────────────────────────────────

const TOOLS = [
  {
    name: "passage_summary_scorer",
    description: "Scores how well a summary captures the key content of a source passage. Returns a score 0-100 with detailed breakdown.",
    inputSchema: {
      type: "object",
      properties: {
        passage: { type: "string", description: "The original source passage" },
        summary: { type: "string", description: "The summary to evaluate" },
      },
      required: ["passage", "summary"],
    },
  },
  {
    name: "grammar_checker",
    description: "Checks text for common grammar, spelling, and style issues. Returns a list of issues with severity levels.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "The text to check for grammar issues" },
      },
      required: ["text"],
    },
  },
  {
    name: "readability_scorer",
    description: "Analyzes text readability using Flesch-Kincaid and other metrics. Returns reading level, grade, and detailed stats.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "The text to analyze for readability" },
      },
      required: ["text"],
    },
  },
];

// ── Tool Handlers ────────────────────────────────────────────────────

function handlePassageSummaryScorer({ passage, summary }) {
  const passageWords = wordCount(passage);
  const summaryWords = wordCount(summary);
  const compressionRatio = summaryWords / Math.max(passageWords, 1);

  const coverage = wordOverlap(passage, summary);

  const passageNouns = passage
    .replace(/[^\w\s]/g, "")
    .split(/\s+/)
    .filter(w => w.length > 4 && w[0] === w[0].toUpperCase());
  const nounsCovered = passageNouns.filter(n =>
    summary.toLowerCase().includes(n.toLowerCase())
  ).length;
  const nounCoverage = passageNouns.length > 0 ? nounsCovered / passageNouns.length : 1.0;

  let compressionScore;
  if (compressionRatio < 0.1) compressionScore = 40;
  else if (compressionRatio < 0.2) compressionScore = 80;
  else if (compressionRatio < 0.4) compressionScore = 100;
  else if (compressionRatio < 0.6) compressionScore = 70;
  else compressionScore = 40;

  const overallScore = Math.round(coverage * 40 + nounCoverage * 30 + compressionScore * 0.3);

  return {
    score: Math.min(100, Math.max(0, overallScore)),
    breakdown: {
      content_coverage: Math.round(coverage * 100),
      key_entity_coverage: Math.round(nounCoverage * 100),
      compression_quality: compressionScore,
    },
    stats: {
      passage_words: passageWords,
      summary_words: summaryWords,
      compression_ratio: Math.round(compressionRatio * 100) / 100,
      key_entities_found: `${nounsCovered}/${passageNouns.length}`,
    },
  };
}

function handleGrammarChecker({ text }) {
  const issues = [];

  for (const rule of GRAMMAR_RULES) {
    let match;
    const regex = new RegExp(rule.pattern.source, rule.pattern.flags);
    while ((match = regex.exec(text)) !== null) {
      const start = Math.max(0, match.index - 15);
      const end = Math.min(text.length, match.index + match[0].length + 15);
      issues.push({
        severity: rule.severity,
        message: rule.message,
        position: match.index,
        context: `...${text.slice(start, end)}...`,
      });
    }
  }

  const wc = wordCount(text);
  const errorRate = issues.filter(i => i.severity === "error").length / Math.max(wc / 100, 1);
  let qualityLabel;
  if (errorRate === 0) qualityLabel = "excellent";
  else if (errorRate < 1) qualityLabel = "good";
  else if (errorRate < 3) qualityLabel = "fair";
  else qualityLabel = "poor";

  return {
    quality: qualityLabel,
    total_issues: issues.length,
    errors: issues.filter(i => i.severity === "error").length,
    warnings: issues.filter(i => i.severity === "warning").length,
    word_count: wc,
    issues: issues.slice(0, 20),
  };
}

function handleReadabilityScorer({ text }) {
  const words = text.split(/\s+/).filter(Boolean);
  const wc = words.length;
  const sc = sentenceCount(text);
  const totalSyllables = words.reduce((s, w) => s + syllableCount(w), 0);
  const avgSyllablesPerWord = totalSyllables / Math.max(wc, 1);
  const avgWordsPerSentence = wc / Math.max(sc, 1);

  const fk = fleschKincaid(text);
  const gradeLevel = 0.39 * avgWordsPerSentence + 11.8 * avgSyllablesPerWord - 15.59;

  let level;
  if (fk >= 90) level = "very easy (5th grade)";
  else if (fk >= 80) level = "easy (6th grade)";
  else if (fk >= 70) level = "fairly easy (7th grade)";
  else if (fk >= 60) level = "standard (8th-9th grade)";
  else if (fk >= 50) level = "fairly difficult (10th-12th grade)";
  else if (fk >= 30) level = "difficult (college)";
  else level = "very difficult (graduate)";

  const complexWords = words.filter(w => syllableCount(w) >= 3).length;

  return {
    flesch_reading_ease: Math.round(fk * 10) / 10,
    flesch_kincaid_grade: Math.round(gradeLevel * 10) / 10,
    reading_level: level,
    stats: {
      word_count: wc,
      sentence_count: sc,
      avg_words_per_sentence: Math.round(avgWordsPerSentence * 10) / 10,
      avg_syllables_per_word: Math.round(avgSyllablesPerWord * 100) / 100,
      complex_word_ratio: Math.round((complexWords / Math.max(wc, 1)) * 100) + "%",
    },
  };
}

const TOOL_HANDLERS = {
  passage_summary_scorer: handlePassageSummaryScorer,
  grammar_checker: handleGrammarChecker,
  readability_scorer: handleReadabilityScorer,
};

// ── JSON-RPC MCP Server (plain JSON, no SSE) ─────────────────────────

const sessions = new Set();

app.post("/mcp", (req, res) => {
  const msg = req.body;

  // Handle initialize
  if (msg.method === "initialize") {
    const sessionId = randomUUID();
    sessions.add(sessionId);
    res.setHeader("MCP-Session-Id", sessionId);
    return res.json({
      jsonrpc: "2.0",
      id: msg.id,
      result: {
        protocolVersion: "2024-11-05",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "text-quality-tools", version: "1.0.0" },
      },
    });
  }

  // Handle initialized notification
  if (msg.method === "notifications/initialized") {
    return res.status(202).end();
  }

  // Handle tools/list
  if (msg.method === "tools/list") {
    return res.json({
      jsonrpc: "2.0",
      id: msg.id,
      result: { tools: TOOLS },
    });
  }

  // Handle tools/call
  if (msg.method === "tools/call") {
    const { name, arguments: args } = msg.params;
    const handler = TOOL_HANDLERS[name];
    if (!handler) {
      return res.json({
        jsonrpc: "2.0",
        id: msg.id,
        error: { code: -32601, message: `Unknown tool: ${name}` },
      });
    }

    try {
      const result = handler(args);
      return res.json({
        jsonrpc: "2.0",
        id: msg.id,
        result: {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        },
      });
    } catch (err) {
      return res.json({
        jsonrpc: "2.0",
        id: msg.id,
        error: { code: -32000, message: err.message },
      });
    }
  }

  // Handle ping
  if (msg.method === "ping") {
    return res.json({ jsonrpc: "2.0", id: msg.id, result: {} });
  }

  // Unknown method
  res.json({
    jsonrpc: "2.0",
    id: msg.id,
    error: { code: -32601, message: `Method not found: ${msg.method}` },
  });
});

// Health check
app.get("/health", (_req, res) => res.json({ status: "ok" }));

const PORT = process.env.PORT || 3456;
app.listen(PORT, "0.0.0.0", () => {
  console.log(`Text Quality MCP Server running on port ${PORT}`);
  console.log(`Tools: passage_summary_scorer, grammar_checker, readability_scorer`);
  console.log(`Endpoint: POST http://localhost:${PORT}/mcp`);
});
