// Mock data for Agent Templates
// This simulates what would come from an API

export const AGENT_TEMPLATE_CATEGORIES = [
  { name: "analytics", displayName: "Analytics" },
  { name: "code", displayName: "Code" },
  { name: "research", displayName: "Research" },
  { name: "product", displayName: "Product" },
  { name: "marketing", displayName: "Marketing" },
  { name: "hr", displayName: "HR" },
];

// Helper to generate graph data for templates
const generateTemplateGraph = () => {
  const data = {
    nodes: [
      {
        id: "start",
        type: "start",
        position: {
          x: 0,
          y: 0,
        },
        measured: {
          width: 30,
          height: 30,
        },
      },
      {
        id: "agent_1",
        type: "agent",
        position: {
          x: 100,
          y: -4,
        },
        data: {
          label: "Agent 1",
          nodeConfig: {
            id: "agent",
            title: "Agent Node",
            description: "Run an agent through LLM",
            iconSrc: "/assets/icons/navbar/ic_agents.svg",
            color: "status.info.fg",
          },
          hasOutgoingEdge: true,
        },
        measured: {
          width: 200,
          height: 38,
        },
        selected: true,
      },
      {
        id: "eval_1",
        type: "eval",
        position: {
          x: 400,
          y: -4,
        },
        data: {
          label: "Eval 1",
          nodeConfig: {
            id: "eval",
            title: "Evaluation Node",
            description: "Evaluate the output",
            iconSrc: "/assets/icons/ic_rounded_square.svg",
            color: "status.success.fg",
          },
          hasOutgoingEdge: false,
        },
        measured: {
          width: 200,
          height: 38,
        },
      },
    ],
    edges: [
      {
        id: "start-agent_1",
        source: "start",
        target: "agent_1",
        type: "sequential",
        deletable: false,
      },
      {
        id: "agent_1-eval_1",
        source: "agent_1",
        target: "eval_1",
        type: "sequential",
      },
    ],
  };

  return data;
};

// Template documentation content as markdown
const TEMPLATE_DOCS = `## Who's it for

This workflow is ideal for AI developers running multi-agent systems in n8n who need to quantitatively evaluate tool usage behavior. If you're building autonomous agents and want to verify their decisions against ground-truth expectations, this workflow gives you plug-and-play observability.

## What it does

- Dataset-driven testing of agent behavior
- Logging actual tools to compare them with the expected tools
- Assigning performance metrics (tool_called = true/false)
- Persisting output back to Google Sheets for further debugging

The workflow can be triggered by either the chat input or the dataset row evaluation. It routes through a multi-tool agent node powered by the best LLMs. The agent has access to tools such as web search, calculator, vector search, and summarizer tools. The workflow then aims to validate tool use decisions by extracting the intermediate steps from the agent (i.e., action + observation) and comparing the tools that were called with the expected tools. If the tools that were called during the workflow execution match, then it's a pass; otherwise, it's documented as a fail. The evaluation nodes take care of that process.

## How to set it up

1. Connect your Google Sheets OAuth2 credential. Replace the document with your own test dataset.
2. Set your desired models and configure the different agent tools, such as the summarizer and vector store. The default vector store used is Qdrant, so the user must create this vector store with a few samples of queries + web search results.
3. Run from either the chat trigger or the evaluation trigger to test.

## Requirements

- Google Sheets OAuth2 credential
- OpenRouter / OpenAI credentials for AI agents and embeddings
- Firecrawl and Qdrant credentials for web + vector search

## How to customize

- Edit the Search Agent system message to define tool selection behavior
- Add more metric columns in the Evaluation node for complex scoring
- Add new tool nodes and link them to the agent block
- Swap in your own summarizer
`;

export const AGENT_TEMPLATES = [
  // Analytics Templates
  {
    id: "analytics-1",
    name: "Data Analyst",
    description:
      "Analyze datasets, generate insights, and create comprehensive reports with visualizations and recommendations.",
    category: "analytics",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Data Analysis" },
        { type: "eval", label: "Quality Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "analytics-2",
    name: "Metrics Reporter",
    description:
      "Generate automated reports on key performance indicators with trend analysis and anomaly detection.",
    category: "analytics",
    createdBy: "FutureAGI",
    config: {
      nodes: [{ type: "prompt", label: "Report Generation" }],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "analytics-3",
    name: "Dashboard Builder",
    description:
      "Create interactive dashboards and data visualizations from complex datasets with automated insights.",
    category: "analytics",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Dashboard Design" },
        { type: "agent", label: "Visualization Agent" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "analytics-4",
    name: "Forecasting Agent",
    description:
      "Predict future trends and outcomes using historical data analysis and statistical modeling techniques.",
    category: "analytics",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Trend Analysis" },
        { type: "eval", label: "Accuracy Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },

  // Code Templates
  {
    id: "code-1",
    name: "Blog Post Writer",
    description:
      "Generate engaging blog posts on any topic with SEO optimization and clear structure.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Prompt_1" },
        { type: "agent", label: "Agent node" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "code-2",
    name: "Email Marketing Specialist",
    description:
      "Design personalized email campaigns that drive conversions with effective call-to-actions and insightful analytics.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Email Draft" },
        { type: "agent", label: "Personalization" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "code-3",
    name: "Content Strategist",
    description:
      "Develop comprehensive content strategies that align with business goals, focusing on audience research and multi-channel distribution.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Strategy Planning" },
        { type: "eval", label: "Goal Alignment" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "code-4",
    name: "Social Media Manager",
    description:
      "Craft compelling social media content tailored for diverse platforms, ensuring audience engagement and brand consistency.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Post Creation" },
        { type: "agent", label: "Platform Optimizer" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "code-5",
    name: "Code Reviewer",
    description:
      "Analyze code for best practices, security vulnerabilities, and performance optimizations with detailed feedback.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Code Analysis" },
        { type: "eval", label: "Security Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "code-6",
    name: "Documentation Generator",
    description:
      "Automatically generate comprehensive documentation from codebases, APIs, and technical specifications.",
    category: "code",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Doc Generation" },
        { type: "eval", label: "Completeness Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },

  // Research Templates
  {
    id: "research-1",
    name: "Market Research Analyst",
    description:
      "Conduct in-depth market analysis with competitor insights, trend identification, and strategic recommendations.",
    category: "research",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Market Analysis" },
        { type: "agent", label: "Competitor Research" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "research-2",
    name: "Academic Researcher",
    description:
      "Assist with literature reviews, citation management, and research paper summarization for academic projects.",
    category: "research",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Literature Review" },
        { type: "eval", label: "Source Verification" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "research-3",
    name: "Competitive Intelligence",
    description:
      "Gather and analyze competitor information to inform business strategy and identify market opportunities.",
    category: "research",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Intel Gathering" },
        { type: "agent", label: "Analysis Agent" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "research-4",
    name: "Survey Designer",
    description:
      "Create effective surveys with proper question design, response options, and bias minimization techniques.",
    category: "research",
    createdBy: "FutureAGI",
    config: {
      nodes: [{ type: "prompt", label: "Survey Creation" }],
    },
    documentation: TEMPLATE_DOCS,
  },

  // Product Templates
  {
    id: "product-1",
    name: "Product Spec Writer",
    description:
      "Create detailed product specifications and requirements documents with user stories and acceptance criteria.",
    category: "product",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Spec Writing" },
        { type: "eval", label: "Requirements Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "product-2",
    name: "Feature Prioritizer",
    description:
      "Analyze and prioritize product features based on business value, user impact, and development effort.",
    category: "product",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Feature Analysis" },
        { type: "agent", label: "Scoring Agent" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "product-3",
    name: "User Story Generator",
    description:
      "Transform product ideas into well-structured user stories with acceptance criteria and technical notes.",
    category: "product",
    createdBy: "FutureAGI",
    config: {
      nodes: [{ type: "prompt", label: "Story Generation" }],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "product-4",
    name: "Roadmap Planner",
    description:
      "Create comprehensive product roadmaps with timeline estimation, dependency mapping, and milestone tracking.",
    category: "product",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Roadmap Planning" },
        { type: "eval", label: "Feasibility Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },

  // Marketing Templates
  {
    id: "marketing-1",
    name: "Campaign Manager",
    description:
      "Plan and execute marketing campaigns with audience targeting, channel selection, and performance tracking.",
    category: "marketing",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Campaign Planning" },
        { type: "agent", label: "Audience Targeting" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "marketing-2",
    name: "SEO Optimizer",
    description:
      "Optimize content for search engines with keyword research, meta tag suggestions, and ranking improvements.",
    category: "marketing",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "SEO Analysis" },
        { type: "eval", label: "Keyword Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "marketing-3",
    name: "Ad Copy Writer",
    description:
      "Create compelling advertising copy for various platforms with A/B testing variations and conversion optimization.",
    category: "marketing",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Copy Generation" },
        { type: "eval", label: "Conversion Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "marketing-4",
    name: "Brand Voice Assistant",
    description:
      "Maintain consistent brand voice across all communications with style guides and tone recommendations.",
    category: "marketing",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Brand Analysis" },
        { type: "agent", label: "Voice Consistency" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },

  // HR Templates
  {
    id: "hr-1",
    name: "Job Description Writer",
    description:
      "Create compelling job descriptions that attract top talent with clear responsibilities and requirements.",
    category: "hr",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "JD Generation" },
        { type: "eval", label: "Bias Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "hr-2",
    name: "Interview Assistant",
    description:
      "Generate relevant interview questions based on job requirements and evaluate candidate responses objectively.",
    category: "hr",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Question Generation" },
        { type: "agent", label: "Response Evaluation" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "hr-3",
    name: "Onboarding Guide",
    description:
      "Create personalized onboarding plans and materials for new employees based on their role and department.",
    category: "hr",
    createdBy: "FutureAGI",
    config: {
      nodes: [{ type: "prompt", label: "Onboarding Plan" }],
    },
    documentation: TEMPLATE_DOCS,
  },
  {
    id: "hr-4",
    name: "Performance Reviewer",
    description:
      "Assist in writing constructive performance reviews with objective feedback and development recommendations.",
    category: "hr",
    createdBy: "FutureAGI",
    config: {
      nodes: [
        { type: "prompt", label: "Review Writing" },
        { type: "eval", label: "Fairness Check" },
      ],
    },
    documentation: TEMPLATE_DOCS,
  },
];

// Mock API functions
export const fetchAgentTemplateCategories = async () => {
  // Simulate network delay
  await new Promise((resolve) => setTimeout(resolve, 300));
  return { data: { result: AGENT_TEMPLATE_CATEGORIES } };
};

export const fetchAgentTemplates = async ({
  category,
  searchQuery,
  pageNumber = 0,
  pageSize = 30,
}) => {
  // Simulate network delay
  await new Promise((resolve) => setTimeout(resolve, 400));

  let filteredTemplates = [...AGENT_TEMPLATES];

  // Filter by category
  if (category && category !== "all") {
    filteredTemplates = filteredTemplates.filter(
      (t) => t.category === category,
    );
  }

  // Filter by search query
  if (searchQuery) {
    const query = searchQuery.toLowerCase();
    filteredTemplates = filteredTemplates.filter(
      (t) =>
        t.name.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query),
    );
  }

  // Pagination
  const start = pageNumber * pageSize;
  const paginatedTemplates = filteredTemplates.slice(start, start + pageSize);

  return {
    data: {
      result: {
        data: paginatedTemplates,
        totalCount: filteredTemplates.length,
      },
    },
  };
};

export const fetchAgentTemplateById = async (templateId) => {
  // Simulate network delay
  await new Promise((resolve) => setTimeout(resolve, 200));

  const template = AGENT_TEMPLATES.find((t) => t.id === templateId);
  if (!template) {
    throw new Error("Template not found");
  }

  return { data: { result: template } };
};

// Fetch template graph data for preview
export const fetchTemplateGraph = async (templateId) => {
  // Simulate network delay
  await new Promise((resolve) => setTimeout(resolve, 500));

  const template = AGENT_TEMPLATES.find((t) => t.id === templateId);
  if (!template) {
    throw new Error("Template not found");
  }

  const graphData = generateTemplateGraph(template.config);

  return {
    data: {
      ...graphData,
      templateId,
      name: template.name,
    },
  };
};

// Helper for cancellable delay
const cancellableDelay = (ms) => {
  return new Promise((resolve) => setTimeout(resolve, ms));
};

// Simulate loading a template into the builder (with progress)
export const loadTemplateToBuilder = async (templateId, onProgress) => {
  const template = AGENT_TEMPLATES.find((t) => t.id === templateId);
  if (!template) {
    throw new Error("Template not found");
  }

  // Simulate loading progress
  const totalSteps = template.config.nodes.length + 2; // +2 for init and finalize
  let currentStep = 0;

  // Step 1: Initialize
  await cancellableDelay(800);
  currentStep++;
  // Call progress callback - it may throw if cancelled
  onProgress?.((currentStep / totalSteps) * 100, "Initializing template...");

  // Step 2-N: Load each node
  for (const node of template.config.nodes) {
    await cancellableDelay(1200);
    currentStep++;
    // Call progress callback - it may throw if cancelled
    onProgress?.((currentStep / totalSteps) * 100, `Loading ${node.label}...`);
  }

  // Final step: Configure connections
  await cancellableDelay(600);
  currentStep++;
  // Call progress callback - it may throw if cancelled
  onProgress?.((currentStep / totalSteps) * 100, "Finalizing workflow...");

  const graphData = generateTemplateGraph(template.config);

  return {
    data: {
      ...graphData,
      templateId,
      name: template.name,
      template,
    },
  };
};
