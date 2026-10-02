# TH-8088 architecture stage: sources checked

All fetches were read-only documentation reads on 2026-10-02 unless marked PM (approved provider matrix r2, checked 2026-09-30, not re-fetched). No provider API call, no credential, no spend.

## Repository (read-only snapshot)
- `/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi` at dev `ce6af27fa72afa793e1e89cfd7a7fea0d753cca0`
- Files inspected: `agentcc-gateway/internal/video/{models.go,store.go}`, `internal/server/{handlers_video.go,handlers_image.go,handlers.go,server.go}`, `internal/providers/{provider.go,registry.go,presets.go,override.go}`, `internal/providers/{gemini/gemini.go,gemini/image.go,bedrock/auth.go,bedrock/translate.go}`, `internal/pipeline/{engine.go,plugin.go}`, `internal/plugins/{auth,budget,cost,credits,ratelimit,quota,rbac}`, `internal/redisstate/{budget.go,client.go,credits.go,cluster.go,ratelimit.go}`, `internal/async/{store.go,worker.go}`, `internal/files/store.go`, `internal/netguard/netguard.go`, `internal/cache/backend_s3.go`, `internal/secrets/resolver.go`, `internal/config/config.go`, `config.example.yaml`, `Makefile`, `go.mod`, `README.md`; `.github/workflows/agentcc-gateway-ci.yml`; `scripts/generate-agentcc-gateway-contracts.py`; `api_contracts/gateway/*`.
- `grep -rn 'v1/videos' futureagi` returned nothing (no Django consumer).

## Approved inputs
- company-brain worktree `/Users/nikhilpareek/.gbrain/worktrees/company-th-8088-video-generation` (HEAD c9ede36, contains PRD r2 self-approval record referencing 5b41783): `engineering/gateway-video-generation/{PRD.md,provider-matrix.md,decisions.md,evidence-ledger.md}`, `brain/org/oss.md`, `brain/org/decision-making.md`, `engineering/CODING-BIBLE.md`.

## Provider documentation (checked 2026-10-02)
- BytePlus ModelArk (content extracted from `window._ROUTER_DATA.curDoc.MDContent`, cached in scratch `th8088/*.md`):
  - https://docs.byteplus.com/en/docs/ModelArk/2298881 (video generation tutorial; page header "Last updated: September 22, 2026")
  - https://docs.byteplus.com/en/docs/ModelArk/create-video-generation-task-api
  - https://docs.byteplus.com/en/docs/ModelArk/get-video-generation-task-api
  - https://docs.byteplus.com/en/docs/ModelArk/list-video-generation-tasks-api
  - https://docs.byteplus.com/en/docs/modelark/seedance-2-5
  - https://docs.byteplus.com/en/docs/ModelArk/1544106 (pricing)
- Google: https://ai.google.dev/gemini-api/docs/veo (2026-09-17), https://ai.google.dev/gemini-api/docs/omni (2026-09-23), https://ai.google.dev/gemini-api/docs/interactions-overview (2026-09-17), https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate, https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/projects.locations.endpoints/predict (redirect target of the old Veo reference; 2026-05-07)
- Runway: https://docs.dev.runwayml.com/openapi.json (646,945 bytes; grep-verified paths, status constants, cost fields, DELETE semantics, version header)
- Luma: https://docs.agents.lumalabs.ai/guides/videos/generation/
- Kling: https://kling.ai/document-api/api/video/3-0-omni/text-to-video.md
- MiniMax: https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- xAI: https://docs.x.ai/developers/model-capabilities/video/generation (2026-09-21)
- AWS: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_StartAsyncInvoke.html
- fal: https://fal.ai/docs/documentation/model-apis/inference/queue (webhook page: PM)
- Alibaba (search-result snapshots of official pages; full bodies to pin at implementation): https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference (Sep 28, 2026), https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-guide, https://www.alibabacloud.com/help/en/model-studio/happyhorse-text-to-video-api-reference, https://www.alibabacloud.com/help/en/model-studio/happyhorse-image-to-video-api-reference, https://www.alibabacloud.com/help/en/model-studio/happyhorse-reference-to-video-api-reference, https://www.alibabacloud.com/help/en/model-studio/use-video-generation (Sep 10, 2026)

## Not re-fetched (PM 2026-09-30 values used, labelled)
- Gemini pricing page, Runway pricing/models guides, Luma pricing grid, MiniMax query/delete pages and pricing, Alibaba pricing, xAI model card, Nova user guide/pricing, fal webhooks/CDN pages, PixVerse, Replicate.

## Tooling evidence
- SVG bounds check: `check_svg_bounds.py` (all rects/texts/paths inside viewBox; no text overflow after fixes).
- PNG render: `rsvg-convert 2.62.1` → 2800x1960; `check_png_edges.py` pixel sampling: right/bottom margins empty, legend band and rightmost boxes present.
- Mermaid sources were not rendered (no mermaid-cli installed; not installed to avoid new network/package installs); syntax reviewed by inspection only.
