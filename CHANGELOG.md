# Changelog

## [1.29.1](https://github.com/future-agi/future-agi/compare/v1.29.0...v1.29.1) (2026-08-24)


### Bug Fixes

* **annotations:** route conversation traces to the voice UI in the queue ([5dc9ff2](https://github.com/future-agi/future-agi/commit/5dc9ff237fe076b218355d16352b37fe6c5f8c70))
* **annotations:** route conversation traces to the voice UI in the queue ([584f6db](https://github.com/future-agi/future-agi/commit/584f6db93c8f936758e0d093aa60ebcf72e8103e))
* **tracer:** make span attribute-key discovery exhaustive over its window [TH-7632] ([39f200a](https://github.com/future-agi/future-agi/commit/39f200aac9c599d3442c82f9627c7bf22e401aee))
* **tracer:** make span attribute-key discovery exhaustive over its window [TH-7632] ([374eb63](https://github.com/future-agi/future-agi/commit/374eb633b032242a2abb9e68b4b91d56b60d645e))
* **tracer:** stop the legacy sample lane inflating attribute key counts [TH-7632] ([ca8416d](https://github.com/future-agi/future-agi/commit/ca8416d6d3a1f085b1a5a4cb488e668a227aef3d))

## [1.28.0](https://github.com/future-agi/future-agi/compare/v1.27.4...v1.28.0) (2026-08-18)


### Features

* **gateway:** attach prompts and completions to exported spans ([9109beb](https://github.com/future-agi/future-agi/commit/9109beb905b360251d967e460b03dcd29031f3f3))
* **gateway:** authenticate OTLP export with configured headers ([c5f0663](https://github.com/future-agi/future-agi/commit/c5f0663d72ad7b19a14fbf26d27c55d0cf620f32))
* **gateway:** cover image, audio, embedding and retrieval endpoints ([be25beb](https://github.com/future-agi/future-agi/commit/be25beb2e2982422f405f5a0eb9e7a3cb313c9b5))
* **gateway:** emit flattened message attributes so traces render ([7ce1bbd](https://github.com/future-agi/future-agi/commit/7ce1bbd0bba83a587879b8ae86193a937775921b))
* **gateway:** export traces over OTLP/HTTP ([0bd97d9](https://github.com/future-agi/future-agi/commit/0bd97d99e251916a4c32f424c4a3059366c68b31))
* **gateway:** export traces over OTLP/HTTP with prompts and completions ([7cbf13c](https://github.com/future-agi/future-agi/commit/7cbf13ce75cfd7fafdbb3d04f333dd4d6fa5f5de))
* **tasks:** add a re-run button to the task detail header [TH-7298] ([#2119](https://github.com/future-agi/future-agi/issues/2119)) ([e780ceb](https://github.com/future-agi/future-agi/commit/e780cebac97c525f27a46e16765d1813fd2e94c7))


### Bug Fixes

* add INTEGRATION_ENCRYPTION_KEY to root .env.example and fix invalid docker-compose default ([#1059](https://github.com/future-agi/future-agi/issues/1059)) ([4247aff](https://github.com/future-agi/future-agi/commit/4247aff0b79ed0f4effd2cf0c0b58d22755ec0db))
* **annotations:** align annotation API response contracts with the serializers ([#2110](https://github.com/future-agi/future-agi/issues/2110)) ([724e3d2](https://github.com/future-agi/future-agi/commit/724e3d2505f8f567629980812446a19472103a1a))
* **annotations:** archive action visibility and settings-tab archive ([#2112](https://github.com/future-agi/future-agi/issues/2112)) ([727b284](https://github.com/future-agi/future-agi/commit/727b284e93b2683227eee47957caa87052abcef9))
* **annotations:** scope annotation label restore by the resolved organization ([#2115](https://github.com/future-agi/future-agi/issues/2115)) ([46dfd95](https://github.com/future-agi/future-agi/commit/46dfd95db96fad24c93428da089f71641366df17))
* dedup voice_call_detail spans with FINAL ([6876938](https://github.com/future-agi/future-agi/commit/6876938125b6902b45f5e92029efd82883ed6dd2))
* **frontend:** render one pie per metric and gate pie on breakdown [TH-6530] ([#2074](https://github.com/future-agi/future-agi/issues/2074)) ([f8af5f2](https://github.com/future-agi/future-agi/commit/f8af5f2ba7df3e58b2a90cd85295ab9bf08cd4c8))
* **gateway:** enforce MCP tool schema validation on empty arguments ([035e07d](https://github.com/future-agi/future-agi/commit/035e07dd568f80f746b50b7096b91f7da0ad5fed))
* **gateway:** retry rate-limited OTLP exports and count unencodable batches ([80cf9c2](https://github.com/future-agi/future-agi/commit/80cf9c27fd20137d7e86e53db3c785215488cc36))
* **gateway:** serialize the redactor cache with the config it caches ([e9fd390](https://github.com/future-agi/future-agi/commit/e9fd390c63156bf8b691171ac3a88a6da3d54997))
* **gateway:** stop post-parallel plugins racing on the request context ([96d0c16](https://github.com/future-agi/future-agi/commit/96d0c167e517f0567ef895f627ae9c4674e424db))
* **gateway:** support large MCP stdio messages ([ebe469c](https://github.com/future-agi/future-agi/commit/ebe469c4cba68de290e6a367f6f82fb97bba60f3))
* **gateway:** truncate span bodies on a rune boundary ([a21bef4](https://github.com/future-agi/future-agi/commit/a21bef40302a24bb08c676ad9e1cac37ad6c5b81))
* make dataset filter chips editable ([#1555](https://github.com/future-agi/future-agi/issues/1555)) ([d594858](https://github.com/future-agi/future-agi/commit/d5948586b69ef31dd9f9d58bee2cd773669e1f3d))
* **signup:** validate work email on the form and widen the domain list [TH-7579] ([#2178](https://github.com/future-agi/future-agi/issues/2178)) ([3bd83b6](https://github.com/future-agi/future-agi/commit/3bd83b6a7009aac2f9384b44311bb2f16f35832b))
* **theme:** make dark-mode checkboxes visible in every state ([#2129](https://github.com/future-agi/future-agi/issues/2129)) ([d46c1d7](https://github.com/future-agi/future-agi/commit/d46c1d7bbed62e8b1d378f4f8afcd7ea169469f9))
* **tracer:** materialize eval_score from structured eval outputs (TH-7492) ([b626ff0](https://github.com/future-agi/future-agi/commit/b626ff0f2c56988466b695806ca4e28c69ed6c0b))

## [1.27.4](https://github.com/future-agi/future-agi/compare/v1.27.3...v1.27.4) (2026-08-13)


### Bug Fixes

* **annotations:** allow clearing numeric label min and max [TH-6991] ([#2090](https://github.com/future-agi/future-agi/issues/2090)) ([d2f6489](https://github.com/future-agi/future-agi/commit/d2f64898eff0f794ccff3cc7497069d1cd8fefe5))
* **annotations:** numeric input bounds, text placeholder, add-items spacing, and badge colors ([#2109](https://github.com/future-agi/future-agi/issues/2109)) ([4e00926](https://github.com/future-agi/future-agi/commit/4e00926a1deb173bd73d31989ccb53ee327f46ab))
* **eval-tasks:** project FK-resolution span reads down to id/trace_id ([ccfe9d2](https://github.com/future-agi/future-agi/commit/ccfe9d2e280146d11900f1711f52d1a1bc980553))
* **eval-tasks:** project FK-resolution span reads down to id/trace_id ([6e22b45](https://github.com/future-agi/future-agi/commit/6e22b45ea9515f66b6db6df1411f439b01c69992))
* **falcon:** inline $ref/$defs in managed stream tool schemas ([140b327](https://github.com/future-agi/future-agi/commit/140b32795d6e155fa75a3457847c32dbfc861d0a))
* **falcon:** inline $ref/$defs in managed stream tool schemas ([e958a45](https://github.com/future-agi/future-agi/commit/e958a45fc1ff5a02248bb7a0b095d3d280018e2d))
* **falcon:** stream managed AI over native gateway SSE ([0c19a5f](https://github.com/future-agi/future-agi/commit/0c19a5f79ed8fb88ca27d69aea3f3c9af245fd4a))
* **falcon:** stream managed AI over native gateway SSE ([6659b92](https://github.com/future-agi/future-agi/commit/6659b92a544b11f426d1ae181e055518b2c41b69))
* **falcon:** stream managed AI over native gateway SSE ([55eba42](https://github.com/future-agi/future-agi/commit/55eba42855dc3d422aeb50e8b162c35968af40b1))
* **falcon:** stream managed AI over native gateway SSE ([e9cb400](https://github.com/future-agi/future-agi/commit/e9cb4008a2733b294a641aff260fd6b9d6c77942))

## [1.27.3](https://github.com/future-agi/future-agi/compare/v1.27.2...v1.27.3) (2026-08-12)


### Bug Fixes

* **temporal:** repoint usage/billing lookups to ee.cloud.temporal ([4f7aaf5](https://github.com/future-agi/future-agi/commit/4f7aaf5817c345c6f9db0de86c66952c7993ddfe))
* **temporal:** repoint usage/billing lookups to ee.cloud.temporal ([#2094](https://github.com/future-agi/future-agi/issues/2094)) ([34d97ac](https://github.com/future-agi/future-agi/commit/34d97ac304ee268a6e1fe62ba3dd7503c2f8aa75))

## [1.27.2](https://github.com/future-agi/future-agi/compare/v1.27.1...v1.27.2) (2026-08-12)


### Bug Fixes

* **eval-tasks:** scope runtime target loads by the task's project (hotfix) ([564181e](https://github.com/future-agi/future-agi/commit/564181e0f3c612e0b433fc622710bcc17a675c0a))

## [1.27.1](https://github.com/future-agi/future-agi/compare/v1.27.0...v1.27.1) (2026-08-12)


### Bug Fixes

* **agents:** clear API key and assistant ID when switching provider [TH-5841] ([#2066](https://github.com/future-agi/future-agi/issues/2066)) ([f777461](https://github.com/future-agi/future-agi/commit/f7774619dbf7d00345b6b12b26282df66d6e591b))
* **collector:** invalidate auth cache on project delete ([2fb25bf](https://github.com/future-agi/future-agi/commit/2fb25bf72ae6c04cd7801944711f8b4658c9dd7c))
* **dashboards:** persist widget series selection across save/reopen ([#1672](https://github.com/future-agi/future-agi/issues/1672)) ([364e6b7](https://github.com/future-agi/future-agi/commit/364e6b74b04641eef230e125d5a2d05c2ca538ac))
* **error-feed:** let the voice trace drawer resize [TH-7491] ([#2067](https://github.com/future-agi/future-agi/issues/2067)) ([42e3499](https://github.com/future-agi/future-agi/commit/42e3499ceaf0c118f54b4d6127aff478384b0e0a))
* **evals:** unblock composite eval model selection in OSS ([#2043](https://github.com/future-agi/future-agi/issues/2043)) ([e175ece](https://github.com/future-agi/future-agi/commit/e175ece17c39131d2150c698476d35cd7812db22))
* **evals:** use snake_case eval_template_id in duplicate eval dialog ([db10cb9](https://github.com/future-agi/future-agi/commit/db10cb9dda19ef3b5bff562a7111429ff5c6d14c))
* **frontend:** darken the yellow eval-score cell for dark theme [TH-7330] ([#2085](https://github.com/future-agi/future-agi/issues/2085)) ([cb14c16](https://github.com/future-agi/future-agi/commit/cb14c16b7d2772937079dcd2ea1f31a0b23226d0))
* **frontend:** move axis assignment above the axis config [TH-6575] ([#2087](https://github.com/future-agi/future-agi/issues/2087)) ([7fad869](https://github.com/future-agi/future-agi/commit/7fad8699502ca3d3c6dd6e6f23a59dfffb37507a))
* **frontend:** only invert black provider logos in dark mode [TH-7274] ([#2011](https://github.com/future-agi/future-agi/issues/2011)) ([4890927](https://github.com/future-agi/future-agi/commit/4890927bbba7a9421c293e777ca1e4738b994758))
* **licensing:** route managed AI through internal gateway on cloud ([5eeb22d](https://github.com/future-agi/future-agi/commit/5eeb22d658288fbbab2b904f4447c4d7c0889871))
* **licensing:** route managed AI through internal gateway on cloud ([8ff49d9](https://github.com/future-agi/future-agi/commit/8ff49d9f74ccd46a6de13857c1e6b2a254f76955))
* **licensing:** route managed AI through internal gateway on cloud ([da1a76b](https://github.com/future-agi/future-agi/commit/da1a76b250f5d5eb09ad48b8e9039c466fa4aad0))
* **licensing:** route managed AI through internal gateway on cloud ([107dedc](https://github.com/future-agi/future-agi/commit/107dedc7bf354631e5b1ec79908942ee17532f9e))
* **oss:** point the sidebar help link at the community Discord in OSS mode [TH-7171] ([#1921](https://github.com/future-agi/future-agi/issues/1921)) ([4bca7c4](https://github.com/future-agi/future-agi/commit/4bca7c4c682ba2f503f81b85deb3a8434f344ee3))
* **oss:** restore preview gate and reason-aware CTA for capability denials ([#2010](https://github.com/future-agi/future-agi/issues/2010)) ([74212fc](https://github.com/future-agi/future-agi/commit/74212fc4787d8019025f40a28f39a8877e2b61c6))
* raise fi-collector gRPC recv cap to 16MiB and log size rejections ([a8778bf](https://github.com/future-agi/future-agi/commit/a8778bf598f27d62ef1ce943a229e885f32f10ba))
* **workbench:** open prompts whose message content is a string [TH-7260] ([#2063](https://github.com/future-agi/future-agi/issues/2063)) ([2bf0d79](https://github.com/future-agi/future-agi/commit/2bf0d79b772486b4246f192c5a1ea81093d2563d))

## [1.27.0](https://github.com/future-agi/future-agi/compare/v1.26.0...v1.27.0) (2026-08-10)


### Features

* **error-feed:** port the ee scanner and cluster-RCA PRs, and make the RCA read path deterministic ([f9d312a](https://github.com/future-agi/future-agi/commit/f9d312ae8b5355bbed9877fcf3bca4f97934c4e2))
* guard experiment CSV downloads ([#1883](https://github.com/future-agi/future-agi/issues/1883)) ([d39098c](https://github.com/future-agi/future-agi/commit/d39098c5338f5b5d443bc34576d95d470dac2d82))


### Bug Fixes

* **evals:** use snake_case eval_template_id in duplicate eval dialog ([#1775](https://github.com/future-agi/future-agi/issues/1775)) ([3491a3e](https://github.com/future-agi/future-agi/commit/3491a3ef0fc73ab0589e950a24d3e0ecaff439ca))
* **feed:** address review — accurate merge docstring, unlocked migration, honest title tests ([59b733c](https://github.com/future-agi/future-agi/commit/59b733c46a9d290d7c7fb87609527da233f40301))
* **feed:** honest cluster status, and never retire a trace whose scan did not run ([8e619f9](https://github.com/future-agi/future-agi/commit/8e619f9d20b5b0b42138bf45f388f653c6577736))
* **feed:** make failed cluster-RCA runs visible, survive socket death, and stop the Fix tab bouncing ([0876873](https://github.com/future-agi/future-agi/commit/087687342ac1492d14c1a389f054d8f64e1458bb))
* **frontend:** fall back to native audio on CORS-blocked playback ([#2022](https://github.com/future-agi/future-agi/issues/2022)) ([12981a2](https://github.com/future-agi/future-agi/commit/12981a2faabd9dfcbcd2071190afd2c5e0759dab))
* **frontend:** omit time for date-only dataset values ([#1772](https://github.com/future-agi/future-agi/issues/1772)) ([c69b4fc](https://github.com/future-agi/future-agi/commit/c69b4fc90cd863c9aa2e3077e1bd8d199c842454))
* **licensing:** restore cloud guard in EEFeatureMiddleware ([9623809](https://github.com/future-agi/future-agi/commit/9623809f42d7de742e8bffc75a03b4610aceb2cf))
* **licensing:** restore cloud guard in EEFeatureMiddleware ([92fb9bb](https://github.com/future-agi/future-agi/commit/92fb9bb05859030f244e676efa523819d0f6531b))
* **model-hub:** address review — create guard key, tooltip show prop, runtime catalog check, boolean swagger contract ([97a50fc](https://github.com/future-agi/future-agi/commit/97a50fc21cb538fe6303850722cb81b03bf8a1bf))
* **model-hub:** cap dataset-optimization list page size at 100 ([ac905c1](https://github.com/future-agi/future-agi/commit/ac905c1a86ff51aec53dabe949326d74743a11a3))
* **model-hub:** handle unavailable models — deprecated flag + block re-runs (TH-7425) ([bbb9898](https://github.com/future-agi/future-agi/commit/bbb9898f327552e6c53a9ebbfb12e8649509ff23))
* **model-hub:** seed default prompt labels on migrate (TH-7261) ([06f003d](https://github.com/future-agi/future-agi/commit/06f003d8504b15f73d2909b5292fd704810315d0))
* **oss-setup:** gate Continue on pre-flight results and apply the new setup copy [TH-7467] ([#2023](https://github.com/future-agi/future-agi/issues/2023)) ([8742885](https://github.com/future-agi/future-agi/commit/874288577ed4a847c17450e459a0ff82bcce9c21))

## [1.26.0](https://github.com/future-agi/future-agi/compare/v1.25.0...v1.26.0) (2026-08-07)


### Features

* **ee:** ship EE code in-repo behind license gating (TH-7256) ([c62c6be](https://github.com/future-agi/future-agi/commit/c62c6be305c461bc1d77d469854760d519e49bf8))


### Bug Fixes

* **accounts:** skip activate_account IP rate limit in OSS mode and add OSS-skip test coverage ([ac12a50](https://github.com/future-agi/future-agi/commit/ac12a50b4a9b1a4f51ae1d423b4e44933d6103e2))
* **accounts:** skip IP rate limiting in OSS mode ([872487e](https://github.com/future-agi/future-agi/commit/872487e6492b9081e928aed20e2b8a424c579a7f))
* **accounts:** skip IP rate limiting in OSS mode ([2e5cf01](https://github.com/future-agi/future-agi/commit/2e5cf016534e21bc242ed7e81e575366e133aeb1))
* **frontend:** sync row highlight with drawer arrow navigation ([253d5df](https://github.com/future-agi/future-agi/commit/253d5df9e2815ddb30f8d38c41cb7307cbd390a0))
* **frontend:** use snake_case dataset_id in HuggingFace import redirect ([09b4f8a](https://github.com/future-agi/future-agi/commit/09b4f8a9ecbed234d57154911ca270887357966d))
* **frontend:** use snake_case dataset_id in HuggingFace import redirect ([82ba8a3](https://github.com/future-agi/future-agi/commit/82ba8a3477b859ae46d19b064d43f3b105d155e8))
* **gateway:** point org config provider links at the real dashboard route [TH-7271] ([#1998](https://github.com/future-agi/future-agi/issues/1998)) ([fa85eec](https://github.com/future-agi/future-agi/commit/fa85eec711a2c421954b95934c880ff5e7260d60))
* **gating:** KB patch stays oss_baseline; reconcile agent-eval block test ([76c7c9c](https://github.com/future-agi/future-agi/commit/76c7c9c834bfe1b7d296151f1cecb05bb32141d3))
* **gating:** restore lost view gates and reconcile tests with two-tier design ([3ed7ea9](https://github.com/future-agi/future-agi/commit/3ed7ea9208ba4be50528d241c0358953fd1fb596))
* **usage:** restore deployment_telemetry_schema wire contract (ee parity) ([ea2c95c](https://github.com/future-agi/future-agi/commit/ea2c95cc8329926f2f7fe0312763aa66041d9f10))

## [1.25.0](https://github.com/future-agi/future-agi/compare/v1.24.3...v1.25.0) (2026-08-07)


### Features

* **annotations:** add View session action for trace/span queue items ([5b7d1a0](https://github.com/future-agi/future-agi/commit/5b7d1a09a06a2778696bc98bf6d80c494de68fd1))
* **frontend:** branded loading screens + click-to-map variable mapping ([#1847](https://github.com/future-agi/future-agi/issues/1847)) ([158acc5](https://github.com/future-agi/future-agi/commit/158acc5665cb6454fab89f32b46da21eacd66136))
* optimized eval-usage backfill script + seed NodeTemplates on migrate (TH-7012, TH-6727) ([7e79aa1](https://github.com/future-agi/future-agi/commit/7e79aa142d460ba3ca1ad009009e7f5d9bd131c0))
* **oss:** self-hosted first-run setup, browser signup and invite links [TH-7217] ([#1919](https://github.com/future-agi/future-agi/issues/1919)) ([772575e](https://github.com/future-agi/future-agi/commit/772575e15b71d9794aea0b77e5877c41ffc92ab2))


### Bug Fixes

* **evals:** show entitlement errors in-cell for OSS agent eval denials ([f843300](https://github.com/future-agi/future-agi/commit/f843300e4b39d3847db82284ed31d985c0c76262))
* **gateway:** use guardrail label for the configure modal title [TH-3989] ([#1885](https://github.com/future-agi/future-agi/issues/1885)) ([0711d79](https://github.com/future-agi/future-agi/commit/0711d79bb171aa988c69c699e573e4ca66aee992))
* **oss:** add dark-theme assets for agent scenario help modal [TH-7273] ([#1927](https://github.com/future-agi/future-agi/issues/1927)) ([66228d2](https://github.com/future-agi/future-agi/commit/66228d274dccf547dac9867403d8239a4994fe6f))
* **oss:** disable Imagine button in detail drawers on OSS ([#1907](https://github.com/future-agi/future-agi/issues/1907)) ([d37b4fe](https://github.com/future-agi/future-agi/commit/d37b4fe5e20d92f40c409554e7700afbcd818c5a))
* **oss:** make trace selection Actions button a filled toolbar pill [TH-7265] ([#1928](https://github.com/future-agi/future-agi/issues/1928)) ([391db7d](https://github.com/future-agi/future-agi/commit/391db7d564bf750693fd3b5a7ea54b440cf7511e))
* **oss:** require a model on every eval save, test and add [TH-7258] ([#1941](https://github.com/future-agi/future-agi/issues/1941)) ([0789638](https://github.com/future-agi/future-agi/commit/07896386ba7e747b3845cf54255b33762d1d7ca3))
* **oss:** size gateway key dialog inputs and stop browser autofill highlight [TH-7272] ([#1925](https://github.com/future-agi/future-agi/issues/1925)) ([b381a13](https://github.com/future-agi/future-agi/commit/b381a132c17130c39f22853be5dcdd28df555b04))
* **oss:** tint annotation label-type chips for dark theme [TH-7263] ([#1942](https://github.com/future-agi/future-agi/issues/1942)) ([ce59eac](https://github.com/future-agi/future-agi/commit/ce59eac769f71c00c09e8497d0a9717ebf4bf62c))
* **oss:** upgrade-gate cropping, upload button label, numeric label zero [TH-7282, TH-7253, TH-7268] ([#1937](https://github.com/future-agi/future-agi/issues/1937)) ([2b6420a](https://github.com/future-agi/future-agi/commit/2b6420a611850f41e0dadd23b1157008f2e851d4))

## [1.24.3](https://github.com/future-agi/future-agi/compare/v1.24.2...v1.24.3) (2026-08-05)


### Bug Fixes

* make tracer tests green in OSS lane and against test CH database ([ca3b5dc](https://github.com/future-agi/future-agi/commit/ca3b5dc53feeb82451bdbc15c1b438ee3db24f78))
* preserve plaintext trace input/output in detail read path ([ff5cd21](https://github.com/future-agi/future-agi/commit/ff5cd219dd8d44bfd47a560d93a08994577d4d8a))
* trace-detail drawer eval score by type (pass/fail + choices) ([95bc9a3](https://github.com/future-agi/future-agi/commit/95bc9a39b711c0206c06e98a0021244f53b4c646))

## [1.24.2](https://github.com/future-agi/future-agi/compare/v1.24.1...v1.24.2) (2026-08-04)


### Bug Fixes

* **backfill:** drop CH optimize-mirror path, document full-table sweep ([511866f](https://github.com/future-agi/future-agi/commit/511866fb19a739dfb56c3bebb34d2833c87a34d2))
* **model_hub:** harden convert and backfill vector-table commands ([2f32706](https://github.com/future-agi/future-agi/commit/2f32706b14143d23681b403fcd999583012a953c))
* **tests:** use has_ee and requires_ee marker instead of hand-rolled path checks ([deef725](https://github.com/future-agi/future-agi/commit/deef725ab55b97984aa05ad1c58732d9253bf91c))
* **tracer:** address Retell PR review comments ([b18f3ca](https://github.com/future-agi/future-agi/commit/b18f3ca1906639d785e711a9fd66899fdd4883bf))
* **tracer:** backfill blank EvalLogger status for legacy successes ([e4ed615](https://github.com/future-agi/future-agi/commit/e4ed615a6049be063e99c04805955a0686e72827))
* **tracer:** clarify numeric parse and cover null watermark ([baef86e](https://github.com/future-agi/future-agi/commit/baef86eb4d27ff082d0184c828e2d8571bf48963))
* **tracer:** migrate retell list-calls to v3 api ([4eeefa9](https://github.com/future-agi/future-agi/commit/4eeefa9ab2e116b9083bff796224ae636cbc7c09))
* **tracer:** restore provider fetch success log ([6529f78](https://github.com/future-agi/future-agi/commit/6529f783908af6eed7d9f0f54b22d64e7b2af6e0))

## [1.24.1](https://github.com/future-agi/future-agi/compare/v1.24.0...v1.24.1) (2026-08-03)


### Bug Fixes

* **agents:** create observability provider for bland agents ([993804d](https://github.com/future-agi/future-agi/commit/993804d20c7f98998efa02d8c0819c4a6811cee1))
* **agents:** create observability provider for bland agents ([e50a83b](https://github.com/future-agi/future-agi/commit/e50a83bb57e19ec54ecbfec6ae7784e72cee2712))
* **annotations:** address submit review — duplicate labels, counts, comments ([8150104](https://github.com/future-agi/future-agi/commit/8150104b204202859a23c22c3f55f1737fee1823))
* **annotations:** de-flake the assign query-count test ([e61c101](https://github.com/future-agi/future-agi/commit/e61c10157488d10b574f238e28b5227bf662f793))
* **annotations:** keep assign's lowest-pk assigned_to, per review ([2588e78](https://github.com/future-agi/future-agi/commit/2588e78841ab63e353f09e741d218a729b3b33cb))
* **eval-tasks:** window continuous tasks on arrival time, not start time ([b6a5258](https://github.com/future-agi/future-agi/commit/b6a5258251537ff083fc3ef9a9679f2485728e13))
* **eval-tasks:** window continuous tasks on arrival time, not start time ([8d29a60](https://github.com/future-agi/future-agi/commit/8d29a602f87e27c33e6055ff7736024ecde17142))
* **observe:** guard unparseable dates so one bad row can't crash the whole page (TH-7181) ([7b6503c](https://github.com/future-agi/future-agi/commit/7b6503c7c9f4c4afcd2533c7e657f9f68e6b9d33))
* **theme:** make dark mode readable across evals, traces and error feed ([#1884](https://github.com/future-agi/future-agi/issues/1884)) ([a683923](https://github.com/future-agi/future-agi/commit/a68392382226003c5bf17bf6160edf00da72cefb))


### Performance Improvements

* **annotations:** batch submit's per-label label read and score upsert ([385a810](https://github.com/future-agi/future-agi/commit/385a8102119820ddc23e0b0294505c41b5142c74))
* **annotations:** batch submit's per-label label read and score upsert ([e2a214f](https://github.com/future-agi/future-agi/commit/e2a214f75391a04db1bc667dce7d32bd850b012e))
* **annotations:** resolve assign's legacy FK in one query instead of per item ([d62fc89](https://github.com/future-agi/future-agi/commit/d62fc89aeb4df99613bfea3998955eb46444cf92))
* **annotations:** resolve assign's legacy FK in one query instead of per item ([98875f5](https://github.com/future-agi/future-agi/commit/98875f5175d6427cdea1fbb16e1055a190cb0079))

## [1.24.0](https://github.com/future-agi/future-agi/compare/v1.23.1...v1.24.0) (2026-07-30)


### Features

* **oss:** ungate optimization and knowledge base, gate Falcon AI at route ([#1868](https://github.com/future-agi/future-agi/issues/1868)) ([79aa5dd](https://github.com/future-agi/future-agi/commit/79aa5ddbc0dcd311a98b98e9dbb3525aa279ddeb))


### Bug Fixes

* **agentcc:** return 404 for cross-tenant actions ([4bf9ae8](https://github.com/future-agi/future-agi/commit/4bf9ae8f7842677c34bfcf03b8308d836acc1f18))
* **agentcc:** return 404 for cross-tenant actions ([4bf9ae8](https://github.com/future-agi/future-agi/commit/4bf9ae8f7842677c34bfcf03b8308d836acc1f18))
* **annotations:** address review on the source_preview backfill ([5f9cab0](https://github.com/future-agi/future-agi/commit/5f9cab089c77933eecf18074dbb46708b15084d7))
* **annotations:** tie the dedup key to the live spans ORDER BY ([ed31aa9](https://github.com/future-agi/future-agi/commit/ed31aa912a5346084327e8b152f83fb22e9377fe))
* **oss:** gate Turing models and Error Localization ([#1870](https://github.com/future-agi/future-agi/issues/1870)) ([525c07a](https://github.com/future-agi/future-agi/commit/525c07a3fbeeef35dd059999dbc4831a6a375ead))
* repair observe test suite drift and drop legacy CH-infra tests ([2e46bf3](https://github.com/future-agi/future-agi/commit/2e46bf30b634df8855da5c124997bf7e08895b76))
* **simulate:** [TH-7080] green simulate test suite in OSS mode (with and without ee/) ([a06de4a](https://github.com/future-agi/future-agi/commit/a06de4a3f2ca591e118bfb51512d0d3c96335236))
* **simulate:** guard scored choice rendering ([494e033](https://github.com/future-agi/future-agi/commit/494e033e1b76a3338580d5fb2602cb242ef6436e))
* **simulate:** match categorical KPI labels ([0e72711](https://github.com/future-agi/future-agi/commit/0e72711f3d093c46f1ffb722695f8e03144bb6a0))
* **simulate:** preserve configured KPI labels ([9c6451f](https://github.com/future-agi/future-agi/commit/9c6451feedbb7f23ae4ed869561551a5473c0f5d))
* **simulate:** render drawer choice lists ([88f2fdd](https://github.com/future-agi/future-agi/commit/88f2fdd5b5a7d441e8aa187c1c5bd7b3cff36bb3))
* **simulate:** render scored choice outputs ([9cb1c80](https://github.com/future-agi/future-agi/commit/9cb1c80a3f9c7944ecffbe5b90ff2430445d82b2))
* **simulate:** restore categorical KPI labels ([7845ad6](https://github.com/future-agi/future-agi/commit/7845ad67b41c9d70b9b51d9584fb4ac89661d40f))
* **simulate:** reuse scored choice readers ([689e375](https://github.com/future-agi/future-agi/commit/689e375533d43d0fe2f23b2d14fefb307f621025))
* **simulate:** skip malformed score outputs ([16857ca](https://github.com/future-agi/future-agi/commit/16857caa19a936e02928fc34e95e7ff9a90b2f9c))
* **simulate:** surface scored-choices dict-output evals in the KPI eval metrics ([6be9fc2](https://github.com/future-agi/future-agi/commit/6be9fc296876a057d4ce348e3d0c847608be9f16))
* **simulate:** validate scored choice payloads ([49cdfaf](https://github.com/future-agi/future-agi/commit/49cdfaf15f078fcf8a9c122177fc0c125a04d754))
* **storage:** pass region for GCS MinIO client ([86502c0](https://github.com/future-agi/future-agi/commit/86502c09c85f31b9cdc85ef574719cbbfb9c7a46))
* **storage:** pass region for GCS MinIO client ([5e94f2e](https://github.com/future-agi/future-agi/commit/5e94f2e9479ca56ad37ccd7f9fe189cd1472d8c7))
* **tracer-tests:** address observe-suite review feedback ([46dc3ac](https://github.com/future-agi/future-agi/commit/46dc3ac812c265122be6e97e19b5b0e336869694))
* **tracer:** avoid shadowing django settings in filter_values ([d9eb5ed](https://github.com/future-agi/future-agi/commit/d9eb5ed97da8c32dca50d5314cf19b6490c216dd))
* **workspaces:** dedupe /accounts/workspace/list/ behind one query key ([#1867](https://github.com/future-agi/future-agi/issues/1867)) ([16a2f9d](https://github.com/future-agi/future-agi/commit/16a2f9d6c863e6ce87106dc1716826ecfd920481))


### Performance Improvements

* **annotations:** annotate the review-thread lookup so the items grid stops querying per row ([d7fcbc9](https://github.com/future-agi/future-agi/commit/d7fcbc9377b2bda56c52c1a9bcd0dd986385a7c4))
* **annotations:** annotate the review-thread lookup so the items grid stops querying per row ([5999f97](https://github.com/future-agi/future-agi/commit/5999f97bd59f0dd995d3ef8bd4ef19710457fe38))
* **annotations:** batch bulk-review's per-item validation and writes ([71039af](https://github.com/future-agi/future-agi/commit/71039aff60ec43235f6112ce60c35ab6205951d9))
* **annotations:** batch bulk-review's per-item validation and writes ([bf1e352](https://github.com/future-agi/future-agi/commit/bf1e3528efb742eaa5c4e7d5810c1b80bed449ed))
* **annotations:** capture the item source preview so the grid stops reading ClickHouse ([f320a11](https://github.com/future-agi/future-agi/commit/f320a11ae56a7bbd7deb0c302dbf6128a344cfce))
* **annotations:** dedup span reads with LIMIT 1 BY instead of FINAL ([15790be](https://github.com/future-agi/future-agi/commit/15790be9e4ebe8a246a3d1b40d44e31b49f2214b))
* **annotations:** dedup span reads with LIMIT 1 BY instead of FINAL ([d705864](https://github.com/future-agi/future-agi/commit/d7058640c723d6fe5c6c7318356a51c49bcf34f3))
* **annotations:** make bulk-review flat — 11 queries at any batch size ([c2fa866](https://github.com/future-agi/future-agi/commit/c2fa8667f9386eb514fcd977a7568e3a02ff53d0))
* **tracer:** filter_values — fixed 7-day window, never-400, indexed search ([506a083](https://github.com/future-agi/future-agi/commit/506a08347e0b6f932d779d0db729d78dfcc155a1))
* **tracer:** hook up filter-value search in the UI, drop redundant lookups ([ad01db3](https://github.com/future-agi/future-agi/commit/ad01db358369195c865d0fb5e1453c09119bfc74))

## [1.23.1](https://github.com/future-agi/future-agi/compare/v1.23.0...v1.23.1) (2026-07-29)


### Bug Fixes

* **ci:** exempt release-please branches from branch-name check ([8ff6658](https://github.com/future-agi/future-agi/commit/8ff6658d3b029adc13a6d92789db237a7c238ad7))
* **release:** bump only GCP regions in deployment, not us/aws ([9a2635a](https://github.com/future-agi/future-agi/commit/9a2635a71b712db6db26ee55e5f5bf4ceb5cb463))
* **release:** bump only the active GCP regions, not decommissioned us/aws ([83b8b30](https://github.com/future-agi/future-agi/commit/83b8b30291c371f72b1f8a0fb9d3139e69e46e45))
* **release:** include serving (embedding) in the deployment bump ([3e2b644](https://github.com/future-agi/future-agi/commit/3e2b6449551868598ff035d6859188365a5c40e5))
* **simulate:** render scored choices eval labels instead of [object Object] ([#1854](https://github.com/future-agi/future-agi/issues/1854)) ([fe2b579](https://github.com/future-agi/future-agi/commit/fe2b57997f60336a529784d90ada0b18b7a7acc5))

## [1.23.0](https://github.com/future-agi/future-agi/compare/v1.22.76...v1.23.0) (2026-07-28)


### Features

* **model-hub:** add claude 5 and gemini 3.x catalog entries ([306c52e](https://github.com/future-agi/future-agi/commit/306c52efebe15267248331816c0bf01090c6bb4e))
* **model-hub:** add claude 5 and gemini 3.x catalog entries ([9b92a96](https://github.com/future-agi/future-agi/commit/9b92a96259bfde159e3360c18e4eaf103224412f))
* **model-hub:** add gemini 3 pro/flash base and image-gen entries ([e439da4](https://github.com/future-agi/future-agi/commit/e439da44f8667054bd215a6c4e7d732b90136eda))
* **models:** register Gemini 3.6 Flash + add pricing for the new models [TH-7193/TH-7195] ([#1818](https://github.com/future-agi/future-agi/issues/1818)) ([be7cc72](https://github.com/future-agi/future-agi/commit/be7cc72a947baacca2dcae9347dbb9fda9ba9ad7))
* **simulate:** add Bland.ai as an inbound voice provider ([08b40f3](https://github.com/future-agi/future-agi/commit/08b40f32e84743cd122066f7236d074561aa5b0c))
* **simulate:** support Bland as an outbound customer provider ([dfa9c72](https://github.com/future-agi/future-agi/commit/dfa9c720c89c868e3e079ce78a612cfd3ee5cd1e))


### Bug Fixes

* **derived-variables:** gate tolerant JSON parsers on structural chars (TH-6975) ([91c8caf](https://github.com/future-agi/future-agi/commit/91c8cafc1dc3dbe2f0e8e8ae7bf87bd7d2417c3d))
* **evals:** derive provider from model in CustomPromptEvaluator, guard call_llm on None provider ([000120f](https://github.com/future-agi/future-agi/commit/000120f792852293631752f217c09555d7deec38))
* **evals:** derive provider from model in CustomPromptEvaluator; guard call_llm on None provider ([ed8cbf6](https://github.com/future-agi/future-agi/commit/ed8cbf6684cea98cdbe9163699a389f219c385ce))
* **evals:** preserve typed/pasted JSON in eval Test Data editor ([#1727](https://github.com/future-agi/future-agi/issues/1727)) ([49c9457](https://github.com/future-agi/future-agi/commit/49c94575bd87a3638623056bb535762d4d1a2821))
* **observe:** reduce list page_size to 25 and trim load-time over-fetch (TH-7155) ([#1747](https://github.com/future-agi/future-agi/issues/1747)) ([4106d33](https://github.com/future-agi/future-agi/commit/4106d33f79f867eb238f1c0dd2aa8e28275a8bea))
* **simulate:** play combined-only voice recordings instead of spinning forever ([4ce647f](https://github.com/future-agi/future-agi/commit/4ce647f7eeb7520ce8e1787839fb30feeddc41f3))
* **test:** drop eslint-disable for a rule this config does not define ([577e07d](https://github.com/future-agi/future-agi/commit/577e07d051b9df0c6ce6696263aba54c8a6feacb))
* **TH-7128:** dataset test suite cleanup — 4 code fixes, 17 failures resolved, 143→64 test consolidation, 10 renames ([8b2271a](https://github.com/future-agi/future-agi/commit/8b2271a81a38caa07104d666bed066a7d785185f))
* **tracer:** scope voice call detail to the request org, prefer rehosted Bland recording ([e52081f](https://github.com/future-agi/future-agi/commit/e52081f4d4a23cd2a6b4a82c7d5e4c6a79413b79))
* **voice:** scope call detail to request org, play combined-only recordings, prefer rehosted Bland URL ([8714d05](https://github.com/future-agi/future-agi/commit/8714d05d353a39ca1c40d5d38869c57c2ac65eea))


### Performance Improvements

* **tracer:** add mapValues bloom indexes for span-attribute filters ([1aa78e5](https://github.com/future-agi/future-agi/commit/1aa78e5ef141d6814119fb74ea069fc53331532e))
* **tracer:** bound attr-filter membership subqueries to project + window ([4f75d61](https://github.com/future-agi/future-agi/commit/4f75d61103993508d5244dae63e3dedcb1151c36))
* **tracer:** scope attr-filter subqueries + mapValues bloom indexes ([a1f4c44](https://github.com/future-agi/future-agi/commit/a1f4c4495711039dd2268c2bc6274aa3a0b41de4))
* **tracer:** serve case-insensitive text filters from a lowered value bloom ([90d5b1a](https://github.com/future-agi/future-agi/commit/90d5b1a0fa2f5719023165547688e17a23b1c265))
