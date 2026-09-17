[English](README.md) · **简体中文**

> ⚠️ **每日构建版本，供早期测试。** 可能存在各种问题。稳定版即将发布 —— 遇到任何问题请提交 issue。

<div align="center">

<a href="https://futureagi.com">
  <img alt="Future AGI — 让 AI 智能体更可靠" src="frontend/public/assets/readme/Logo.png" width="100%">
</a>

# AI 智能体会产生幻觉。更快地修复它。

**用于交付自我改进型 AI 智能体的开源平台。** 评估（Evaluation）、追踪（Tracing）、仿真（Simulation）、护栏（Guardrails）、网关（Gateway）、优化（Optimization）。所有功能运行在同一个平台、同一个反馈闭环上，从第一个原型到生产环境部署。

<p>
  <a href="https://github.com/future-agi/future-agi/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="Apache 2.0 License"></a>
  <a href="https://pypi.org/project/ai-evaluation/"><img src="https://img.shields.io/pypi/v/ai-evaluation?style=flat-square&label=pypi" alt="PyPI"></a>
  <a href="https://www.npmjs.com/package/@traceai/fi-core"><img src="https://img.shields.io/npm/v/@traceai/fi-core?style=flat-square&label=npm" alt="npm"></a>
  <a href="https://discord.com/invite/n2tCUKBkAw"><img src="https://img.shields.io/badge/discord-join-5865F2?style=flat-square" alt="Discord"></a>
</p>

<p>
  <a href="https://app.futureagi.com/auth/jwt/register"><b>试用云端版（免费）</b></a> ·
  <a href="#-快速上手60-秒"><b>自托管部署</b></a> ·
  <a href="https://docs.futureagi.com"><b>文档</b></a> ·
  <a href="https://futureagi.com/blog"><b>博客</b></a> ·
  <a href="https://discord.com/invite/n2tCUKBkAw"><b>Discord</b></a> ·
  <a href="https://github.com/orgs/future-agi/discussions"><b>讨论区</b></a>
</p>

</div>

---

<div align="center">
  <a href="https://www.youtube.com/watch?v=6keOTAOUUWI">
    <img alt="观看演示 —— 在一个平台上完成智能体追踪、评估、仿真与防护" src="frontend/public/assets/readme/demo-6keOTAOUUWI-v4.png" width="100%">
  </a>
</div>

---

## 为什么选择 Future AGI？

大多数 AI 智能体在生产环境中失败，而团队不得不把评估、可观测性和护栏工具拼凑在一起，这些工具之间永远无法形成闭环。
Future AGI 将这一切统一到一个平台、一个反馈闭环中。在发布前仿真边缘场景，评估生产环境中发生的问题，实时保护用户，并将每一条追踪转化为下一版本的改进信号。
结果是：智能体不仅被监控，还能自我改进。

<table>
<tr>
<td width="33%" valign="top">

###  一体化平台
不再需要拼凑 Langfuse + Braintrust + Helicone + Guardrails AI + 自研仿真器。一个平台覆盖完整生命周期：**仿真 → 评估 → 防护 → 监控 → 优化**，数据回流形成闭环。

</td>
<td width="33%" valign="top">

###  开放且可自托管
Apache 2.0 开源核心。每一个评估器、每一个提示词、每一条追踪都可审计 —— **没有黑盒评分**。可选择自托管保障数据主权，或使用我们的托管云端服务。任何一层都可以通过 OTel / OpenAI 兼容 HTTP 接入你自己的技术栈。

</td>
<td width="33%" valign="top">

###  为生产环境而生
基于 Go 的网关，**~9.9 纳秒加权路由**，**t3.xlarge 上 ~29k 请求/秒**，**开启护栏时 P99 ≤ 21 毫秒**。原生支持 OpenTelemetry 追踪。50+ 框架插桩器。每一项性能声明都可通过仓库内置的基准测试工具复现。

</td>
</tr>
</table>

---

## 🚀 快速上手（60 秒）

根据你想安装的内容多少，有两种方式：

自托管路径要求在运行安装脚本前已安装 Docker Desktop 或 Docker Engine（含 Docker Compose）。

<table width="100%">
<tr>
<th width="50%">云端版（最快）</th>
<th width="50%">自托管（Docker）</th>
</tr>
<tr valign="top">
<td width="50%">

**无需安装。免费套餐。**

```bash
# 免费注册：
#   app.futureagi.com

pip install ai-evaluation
```

<sub>SOC 2 Type II · HIPAA · 数据保留在你所在区域。</sub>

</td>
<td width="50%">

**一条命令，完整技术栈。使用已发布的镜像，无需构建源码。**

```bash
# macOS / Linux / WSL
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/install

# Windows（PowerShell）
git clone https://github.com/future-agi/future-agi.git
cd future-agi
.\bin\install.ps1
```

打开 [http://localhost:3000](http://localhost:3000)。
<sub>生产环境部署请使用 `./deploy/setup.sh` 生成所需密钥并锁定镜像版本。</sub>

升级一个已包含追踪数据的安装时，请在新技术栈健康运行后，显式初始化所有未激活的统一属性目录：

```bash
# macOS / Linux / WSL
./bin/property-catalog-backfill --execute

# Windows PowerShell
.\bin\property-catalog-backfill.ps1 -Execute
```

普通重启永远不会启动历史扫描。该命令使用 Docker Compose 已选定的镜像（不会拉取分支、源码或镜像），跳过已激活的工作区，并通过目录的持久化账本恢复执行。其执行范围限于自托管监管器允许的活动工作区与项目，以及其滚动的 366 天源数据窗口。

</td>
</tr>
</table>

### 接入你的第一个智能体

<table width="100%">
<tr>
<td width="50%">

**Python**
```python
from fi_instrumentation import register
from traceai_openai import OpenAIInstrumentor

register(project_name="my-agent")
OpenAIInstrumentor().instrument()

# 你现有的 OpenAI 代码现在已被追踪。
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": query}],
)
```

</td>
<td width="50%">

**TypeScript**
```typescript
import { register } from "@traceai/fi-core";
import { OpenAIInstrumentation } from "@traceai/openai";

register({ projectName: "my-agent" });
new OpenAIInstrumentation().instrument();

// 你现有的 OpenAI 代码现在已被追踪。
const response = await openai.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: query }],
});
```

</td>
</tr>
</table>

<sub> [完整文档 →](https://docs.futureagi.com)  ·  [实践指南 →](https://docs.futureagi.com/docs/cookbook)  ·  [API 参考 →](https://docs.futureagi.com/docs/api)</sub>

---

## 核心功能

六大支柱。每一项都替代一个你可能正在使用的工具。

<table>
<tr>
<td width="33%" valign="top">

### 🧪 仿真（Simulate）
针对逼真用户画像、对抗性输入和边缘场景，生成数千轮多轮对话。支持文本**和语音**（LiveKit、VAPI、Retell、Pipecat）。

<sub>[文档 →](https://docs.futureagi.com/docs/simulation)</sub>

</td>
<td width="33%" valign="top">

### 📊 评估（Evaluate）
一次 `evaluate()` 调用即可使用 50+ 指标：忠实度、幻觉、工具调用正确性、PII、语气、自定义评分规则。**LLM-as-judge + 启发式 + 机器学习。**

<sub>[文档 →](https://docs.futureagi.com/docs/evaluation)</sub>

</td>
<td width="33%" valign="top">

### 🛡️ 防护（Protect）
18 个内置扫描器（PII、越狱、注入等）+ 15 个供应商适配器（Lakera、Presidio、Llama Guard 等）。可在网关内联使用，或作为独立 SDK。

<sub>[文档 →](https://docs.futureagi.com/docs/protect)</sub>

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 👁️ 监控（Monitor）
原生支持 OpenTelemetry 的追踪，覆盖 50+ 框架（LangChain、LlamaIndex、CrewAI、DSPy 等）。Span 图、延迟、Token 成本、实时仪表盘。零配置。

<sub>[文档 →](https://docs.futureagi.com/docs/observe)</sub>

</td>
<td width="33%" valign="top">

### 🎛️ 智能体指挥中心（Agent Command Center）
OpenAI 兼容网关。100+ 供应商，15 种路由策略，语义缓存，虚拟密钥，MCP，A2A。**~29k 请求/秒，开启护栏时 P99 ≤ 21 毫秒。**

<sub>[文档 →](https://docs.futureagi.com/docs/command-center) · [基准测试 →](./agentcc-gateway/README.md#-benchmarks)</sub>

</td>
<td width="33%" valign="top">

### 🔁 优化（Optimize）
六种提示词优化算法（GEPA、PromptWizard、ProTeGi、贝叶斯、Meta-Prompt、随机搜索）。生产环境的追踪数据回流作为训练数据。

<sub>[文档 →](https://docs.futureagi.com/docs/optimization)</sub>

</td>
</tr>
</table>

---

##  部署选项

| 目标平台 | 状态 | 说明 |
|---|:---:|---|
|  Docker Compose | ✅ | 全新克隆后 `docker compose up -d` 即可使用已发布的镜像 |
|  生产环境 Compose 覆盖层 | ✅ | `./deploy/setup.sh` 生成密钥、锁定镜像标签、拉取镜像并启动技术栈 |
|  Kubernetes / Helm | ⏳ | 官方清单与 Helm Chart 即将推出 |
|  AWS / GCP / Azure | ✅ | 目前可在虚拟机上运行 Docker Compose；托管 Kubernetes 支持即将推出 |
|  AWS Marketplace | ⏳ | 即将推出 |
|  气隙 / 本地部署 | ✅ | 无遥测上报 —— [联系销售](mailto:sales@futureagi.com) |

---

##  架构

每条链路都是开放、有文档的接口：**OpenTelemetry OTLP** 用于追踪，**OpenAI 兼容 HTTP** 用于网关，**Postgres / ClickHouse SQL** 用于存储。任何一层都可以替换为你自己的技术栈。

**运行时：** Python 3.11+（Django 5.1 + Channels）· Go 1.23+（网关）· React 18 + Vite · Node 20+。
**数据：** PostgreSQL（元数据）· ClickHouse（Span + 时序数据）· Redis（状态）· RabbitMQ + Temporal（任务队列）。

<details><summary>组件明细（按包划分）</summary>

| 层 | 组件 | 代码 |
|---|---|---|
|  边缘层 | **traceAI** —— OpenTelemetry 插桩 | [`future-agi/traceAI`](https://github.com/future-agi/traceAI) |
|  边缘层 | **Agent Command Center** —— OpenAI 兼容代理 | [`agentcc-gateway/`](./agentcc-gateway) |
|  平台层 | **tracer** —— OTLP 摄取、Span 图 | [`futureagi/tracer/`](./futureagi/tracer) |
|  平台层 | **agentic_eval** —— 50+ 指标、LLM-as-judge | [`futureagi/agentic_eval/`](./futureagi/agentic_eval) |
|  平台层 | **simulate** —— 用户画像驱动的场景生成 | [`futureagi/simulate/`](./futureagi/simulate) |
|  平台层 | **model_hub** —— LLM 路由、Embedding、数据集 | [`futureagi/model_hub/`](./futureagi/model_hub) |
|  平台层 | **accounts · usage · integrations** —— 认证、组织、计量、连接器 | [`futureagi/accounts/`](./futureagi/accounts) |
|  数据层 | **PostgreSQL** · **ClickHouse** · **Redis** · **RabbitMQ + Temporal** | — |

</details>

---

##  SDK 与集成

Future AGI 是一个**开源生态系统** —— 每个 SDK 都可独立使用、独立打包，采用 Apache/MIT 许可证。

### 客户端库

| 仓库 | 安装 | 语言 | 用途 |
|---|---|---|---|
| [**traceAI**](https://github.com/future-agi/traceAI) | `pip install fi-instrumentation-otel`<br>`npm i @traceai/fi-core` | Python · TS · Java · C# | 面向 50+ AI 框架的**零配置 OTel 追踪** |
| [**ai-evaluation**](https://github.com/future-agi/ai-evaluation) | `pip install ai-evaluation`<br>`npm i @future-agi/ai-evaluation` | Python · TS | **50+ 评估指标** + 护栏扫描器 |
| [**futureagi**](https://github.com/future-agi/futureagi-sdk) | `pip install futureagi` | Python | 平台 SDK —— 数据集、提示词、知识库、实验 |
| [**agent-opt**](https://github.com/future-agi/agent-opt) | `pip install agent-opt` | Python | **6 种提示词优化算法**（GEPA、PromptWizard 等） |
| [**simulate-sdk**](https://github.com/future-agi/simulate-sdk) | `pip install agent-simulate` | Python | 基于 LiveKit + Silero VAD 的语音智能体仿真 |
| [**agentcc**](https://github.com/future-agi/agent-command-center-sdk) | `pip install agentcc`<br>`npm i @agentcc/client` | Python · TS（+ LangChain · LlamaIndex · React · Vercel） | 网关客户端 SDK |

### 集成

| | |
|---|---|
| **LLM 供应商（100+）** | OpenAI · Anthropic · Google Gemini · Vertex AI · AWS Bedrock · Azure OpenAI · Mistral · Groq · Cohere · Together · Perplexity · OpenRouter · Fireworks · xAI · Replicate · HuggingFace · + 自托管 **Ollama · vLLM · LM Studio · TGI · Llamafile** |
| **智能体框架** | LangChain · LangGraph · LlamaIndex · CrewAI · AutoGen · Phidata · PydanticAI · Claude SDK · LiteLLM · Haystack · DSPy · Instructor · Smol-agents |
| **语音平台** | VAPI · Retell · LiveKit · Pipecat |
| **向量数据库** | Pinecone · Weaviate · Chroma · Milvus · Qdrant · pgvector |
| **工具与基础设施** | Vercel AI SDK · n8n · MongoDB · MCP · A2A · Guardrails AI · Langfuse · HuggingFace Smol-agents |

<sub> [完整集成目录 →](https://docs.futureagi.com/docs/integrations)</sub>

---

##  Future AGI 对比

<table width="100%">
<thead>
<tr>
<th width="25%"></th>
<th width="15%" align="center"><b>Future&nbsp;AGI</b></th>
<th width="15%" align="center">Langfuse</th>
<th width="15%" align="center">Phoenix</th>
<th width="15%" align="center">Braintrust</th>
<th width="15%" align="center">Helicone</th>
</tr>
</thead>
<tbody>
<tr><td>开源</td><td align="center">✅<br><sub>Apache 2.0</sub></td><td align="center">✅<br><sub>MIT</sub></td><td align="center">✅<br><sub>Elastic v2</sub></td><td align="center">❌</td><td align="center">✅<br><sub>Apache 2.0</sub></td></tr>
<tr><td>自托管</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">❌</td><td align="center">✅</td></tr>
<tr><td>LLM 追踪（OpenTelemetry）</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">⚠️<br><sub>通过 OpenLLMetry</sub></td></tr>
<tr><td>评估套件</td><td align="center">✅<br><sub>50+ 指标</sub></td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">⚠️<br><sub>有限</sub></td></tr>
<tr><td><b>智能体仿真</b></td><td align="center">✅</td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td></tr>
<tr><td><b>语音智能体评估</b></td><td align="center">✅</td><td align="center">❌</td><td align="center">⚠️<br><sub>实践指南</sub></td><td align="center">❌</td><td align="center">❌</td></tr>
<tr><td><b>内置 LLM 网关</b></td><td align="center">✅<br><sub>100+ 供应商</sub></td><td align="center">❌</td><td align="center">❌</td><td align="center">✅</td><td align="center">✅</td></tr>
<tr><td><b>内置护栏</b></td><td align="center">✅<br><sub>18 + 15 适配器</sub></td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td></tr>
<tr><td><b>提示词优化</b></td><td align="center">✅<br><sub>6 种算法</sub></td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td><td align="center">❌</td></tr>
<tr><td>提示词管理</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td></tr>
<tr><td>数据集与实验</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td><td align="center">✅</td></tr>
<tr><td>无代码评估构建器</td><td align="center">✅</td><td align="center">⚠️</td><td align="center">⚠️</td><td align="center">⚠️</td><td align="center">⚠️</td></tr>
</tbody>
</table>

<sub>基于截至 2026 年 4 月的公开文档功能。欢迎指正 —— 提交 PR 即可。</sub>

---

## 适用于各类智能体

- **客户支持：** 交付客户真正信任的支持型 AI
- **语音智能体：** 端到端测试、评估并改进语音 AI
- **内部工具：** 让整个组织都能依赖的 AI 助手
- **RAG 与搜索：** 每个答案有据可查，每条引用经过验证
- **自主智能体：** 在生产环境中真正可信赖的多步智能体
- **计算机操作智能体（CUA）：** 自信地完成点击操作的智能体
- **编程智能体：** 能交付可上线代码的 AI

---

##  路线图

[**为公开路线图投票 →**](https://futureagi.com/roadmap)  ·  [**GitHub 讨论区**](https://github.com/orgs/future-agi/discussions/categories/roadmap)  ·  [**版本发布**](https://github.com/future-agi/future-agi/releases)  ·  [**更新日志**](https://docs.futureagi.com/docs/release-notes)

<table>
<tr>
<th width="25%"> 最近发布</th>
<th width="25%"> 进行中</th>
<th width="25%"> 即将推出</th>
<th width="25%"> 探索中</th>
</tr>
<tr valign="top">
<td>

- [x] 提示词优化引擎
- [x] 基于分类法的反馈聚类
- [x] 数据集实验中的智能体运行
- [x] 从生产通话生成仿真
- [x] 通过 UI 配置 LiveKit
- [x] 语音系统指标过滤
- [x] 智能体试验场（Agent Playground）
- [x] 仪表盘
- [x] 通过 MCP 访问平台
- [x] 标注队列
- [x] 指挥中心（Command Center）
- [x] 开源 Future AGI 技术栈
- [x] 评估解释输出大小控制

</td>
<td>

- [ ] 智能体变更日志与差异视图
- [ ] 智能队列分配
- [ ] 智能体构建器的必备节点库
- [ ] 智能体全链路执行追踪
- [ ] 智能体多模态支持

</td>
<td>

- [ ] 智能体变更日志与差异视图
- [ ] 智能队列分配

</td>
<td>

- [ ] 将智能体导入试验场
- [ ] 仿真 CUA 智能体
- [ ] 仿真编程智能体
- [ ] 定时仿真

</td>
</tr>
</table>

---

## 🤝 贡献

我们欢迎各种形式的贡献 —— Bug 修复、新评估器、框架集成、文档、示例，任何内容都可以。

1.  [浏览 `good first issue`](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
2.  阅读[贡献指南](CONTRIBUTING.md)
3.  在 [Discord](https://discord.com/invite/n2tCUKBkAw) 或[讨论区](https://github.com/orgs/future-agi/discussions) 打个招呼
4.  你的第一个 PR 会自动触发 CLA 签署机器人

---

## 🌍 社区与支持

| | |
|---|---|
| 💬 [**Discord**](https://discord.com/invite/n2tCUKBkAw) | 来自团队和社区的实时帮助 |
| 🗨️ [**GitHub 讨论区**](https://github.com/orgs/future-agi/discussions) | 想法、问题、路线图建议 |
| 🐦 [**Twitter / X**](https://x.com/FutureAGI_) | 版本发布公告 |
| 📝 [**博客**](https://futureagi.com/blog) | 工程与研究文章 |
| 📺 [**YouTube**](https://www.youtube.com/@Future_AGI) | 演示与教程 |
| 📊 [**服务状态**](https://status.futureagi.com) | 云端可用性 + 事故历史 |
| 📧 **support@futureagi.com** | 云端账户 / 计费问题 |
| 🔐 **security@futureagi.com** | 私密漏洞披露（24 小时内确认 —— 见 [SECURITY.md](SECURITY.md)） |

---

##  遥测

自托管的 Future AGI 会收集部署遥测数据，帮助我们规划发布测试并了解功能采用情况。**绝不收集追踪数据、提示词、API 密钥。**

**收集内容：**
- **注册**（首次启动时一次）：实例 ID、版本、部署类型，以及活跃管理员用户的**电子邮箱地址和域名**。
- **心跳**（周期性）：匿名聚合的使用计数。

在 `.env` 中设置 `FUTURE_AGI_TELEMETRY_DISABLED=1`（生产覆盖层使用 `deploy/.env.production`）即可退出。退出后，实例仍会在首次启动后发送一次最简人口统计请求 —— 仅包含实例 ID、版本、部署类型，**不含邮箱** —— 之后不再发送心跳。该请求用于统计自托管安装数量；如需完全静默，可在网络边界断网。

---

## ⭐ Star 历史

<a href="https://star-history.com/#future-agi/future-agi">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date&theme=dark">
    <img alt="Star 历史" src="https://api.star-history.com/svg?repos=future-agi/future-agi&type=Date">
  </picture>
</a>

---

## 📄 许可证

Future AGI 采用 **Apache License 2.0** 许可证。见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。

**你的评估逻辑和数据归你所有。** 审计每一个评估器、每一个提示词、每一条追踪 —— 没有黑盒评分，没有供应商锁定。

---

<div align="center">

**由 Future AGI 团队和[全球贡献者](https://github.com/future-agi/future-agi/graphs/contributors)用 ❤️ 构建。**

如果 Future AGI 帮助你交付了更好的 AI，点个 ⭐ 能让更多团队发现我们。

[🌐 futureagi.com](https://futureagi.com) · [📖 docs.futureagi.com](https://docs.futureagi.com) · [☁️ app.futureagi.com](https://app.futureagi.com) · [📊 status.futureagi.com](https://status.futureagi.com)

</div>
