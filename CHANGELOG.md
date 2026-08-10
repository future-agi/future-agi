# Changelog

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
