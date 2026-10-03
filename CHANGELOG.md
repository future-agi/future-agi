# Changelog

## [1.44.0](https://github.com/future-agi/future-agi/compare/v1.43.3...v1.44.0) (2026-10-01)


### Features

* **simulate:** read HARNESS_MAX_EXECUTIONS_PER_RUN from the environment ([76386dc](https://github.com/future-agi/future-agi/commit/76386dc684fdac3f705227213c3739e9a6f420dc))
* **simulate:** read HARNESS_MAX_EXECUTIONS_PER_RUN from the environment ([9b232e8](https://github.com/future-agi/future-agi/commit/9b232e8039eb31847ec703b80717f4cae3b82ddb))


### Bug Fixes

* bound Error Feed ClickHouse trace lookups ([db069a9](https://github.com/future-agi/future-agi/commit/db069a9d249fd675b2e6499bd0e0a48cd6dd1acb))
* bound Error Feed ClickHouse trace lookups ([21411fd](https://github.com/future-agi/future-agi/commit/21411fd3cc3f0638045dddd6dd62ddcf3749c269))
* **harness:** apply artifact budget floor to every hosted job; stream offline recovery ([207661e](https://github.com/future-agi/future-agi/commit/207661e9e077e6910610ac45f3d259503d6dbf57))
* **harness:** configure artifact budget through environment ([866bdc2](https://github.com/future-agi/future-agi/commit/866bdc25461adeb7a00032812dd835ec78a5b472))
* **harness:** configure hosted artifact budget via environment ([ca5f591](https://github.com/future-agi/future-agi/commit/ca5f5919d77a9cd26134c3ea3bb56835a68f6ade))
* **harness:** inflate the offline spool while downloading so replay stays linear ([c6a65a4](https://github.com/future-agi/future-agi/commit/c6a65a467ccf8325e55a1b6f661b7ada3c474ed1))

## [1.43.3](https://github.com/future-agi/future-agi/compare/v1.43.2...v1.43.3) (2026-09-30)


### Bug Fixes

* **simulate:** provision a finished suite when rows indexed for dropped scenarios remain ([dccb76b](https://github.com/future-agi/future-agi/commit/dccb76b00c34999f49db4ca21d1d283f625ff936))
* **simulate:** provision a finished suite when rows indexed for dropped scenarios remain ([39fc7a0](https://github.com/future-agi/future-agi/commit/39fc7a0ad0ae6c52075b35250adacadee4021e8a))

## [1.43.2](https://github.com/future-agi/future-agi/compare/v1.43.1...v1.43.2) (2026-09-30)


### Bug Fixes

* **images:** install libgomp1 in the serving runtime stage ([1bc9801](https://github.com/future-agi/future-agi/commit/1bc9801b4530f25d3f8bcda759bd60761b16a046))
* **images:** install libgomp1 in the serving runtime stage ([65d1e5b](https://github.com/future-agi/future-agi/commit/65d1e5b47c449952e25583391bab98c9022c4ca6))

## [1.43.1](https://github.com/future-agi/future-agi/compare/v1.43.0...v1.43.1) (2026-09-30)


### Bug Fixes

* **images:** keep .pyi stubs in the serving image ([59db1e9](https://github.com/future-agi/future-agi/commit/59db1e940308e806f93bce4efc5353d7229a0a83))
* **images:** keep .pyi stubs in the serving image ([7a14f32](https://github.com/future-agi/future-agi/commit/7a14f32266324552f7281b5465951e2abb03ea8f))

## [1.43.0](https://github.com/future-agi/future-agi/compare/v1.42.0...v1.43.0) (2026-09-30)


### Features

* **config:** keep the environment reference as data in deploy/env-reference.toml ([27b4c61](https://github.com/future-agi/future-agi/commit/27b4c617ca823df59ad5bd161fa320270f889507))
* **deploy:** light Standalone install by default, Distributed at scale, Helm chart and hot-reload dev ([cd91301](https://github.com/future-agi/future-agi/commit/cd9130183012b63cff67da859386e2521ff8b169))
* **helm:** publish the chart (signed OCI) with production and enterprise options ([6defc7e](https://github.com/future-agi/future-agi/commit/6defc7e66c389393964c6b91ce919dc0956b1774))
* **scripts:** render the self-hosting reference pages for docs.futureagi.com ([fcfb98b](https://github.com/future-agi/future-agi/commit/fcfb98bce96a9d5bcc1c22f559abdc5fc13ae399))
* **simulate:** configure caller barge-in rate for hosted runs ([636686b](https://github.com/future-agi/future-agi/commit/636686bb175d85ef2fdb47465974ae4e2c72513c))
* **simulate:** forward platform caller barge-in setting to hosted harness ([13d45ed](https://github.com/future-agi/future-agi/commit/13d45edab8f4fcf888042ef786f8b2ac198c95b0))


### Bug Fixes

* **cdc:** accept PG smallint in the PeerDB source contract ([22ce670](https://github.com/future-agi/future-agi/commit/22ce6703a3de0dbdec6cbf3dc99010b68ea54c48))
* **cdc:** accept PG smallint in the PeerDB source contract ([076424b](https://github.com/future-agi/future-agi/commit/076424beb64eba2df02068bd2f016207dd52f8c0))
* **cdc:** accept PG smallint in the PeerDB source contract ([000a668](https://github.com/future-agi/future-agi/commit/000a6684d2271cc48ab80e2ca9e2387180d87171))
* **cdc:** accept PG smallint in the PeerDB source contract ([a92506b](https://github.com/future-agi/future-agi/commit/a92506beb19f6c20841d19eefcf3b50d38437765))
* **cdc:** re-copy re-armed tables, keep mid-sweep reconcile requests, backfill added columns ([6744271](https://github.com/future-agi/future-agi/commit/674427150d96018b036424816841f8a29cb5de7c))
* **contracts:** let the generated bulk-key expires_at be null ([c237799](https://github.com/future-agi/future-agi/commit/c237799378f169fae5933bfd3ee748494232e8ea))
* **deploy:** forward SIGTERM so the backend drains in-flight requests ([1a42948](https://github.com/future-agi/future-agi/commit/1a42948befc1641fe9b87baea99c40296a00b055))
* **deploy:** register Temporal schedules on every deploy and keep operator pauses ([cda3e25](https://github.com/future-agi/future-agi/commit/cda3e25dc4b5679c838be0de9eac358f762d69fb))
* **gateway:** deliver buffered request logs on shutdown; no panic after close; bounded log buffer ([d8249b0](https://github.com/future-agi/future-agi/commit/d8249b09ff52829a36dbdb76cc12ea02fe3c467b))
* **gateway:** send an org's key only to its own base URL; opt-in private provider URLs; keys survive restarts ([aaa51cc](https://github.com/future-agi/future-agi/commit/aaa51ccf8a0f7756ef6c5b3931c1c871f5621255))
* **helm:** redact whole secret values in support bundles; list upstream images for the air-gap mirror ([6934acc](https://github.com/future-agi/future-agi/commit/6934acc3bf023815ad8f3f651e0d7ce21f0f4df8))
* **helm:** refuse an LLM gateway host shared with the app or API host ([97f5660](https://github.com/future-agi/future-agi/commit/97f5660431aa8991bf2e27f27e835c5f48a376da))
* **helm:** run the bootstrap job after bundled datastores under Argo CD ([b12a542](https://github.com/future-agi/future-agi/commit/b12a5425efe1379ce7e42527683fe439855fb30c))
* **install:** no false "install failed" on an install without a ClickHouse container ([ba3a9c4](https://github.com/future-agi/future-agi/commit/ba3a9c46d397b706470bc8e124932ccab0ea39f1))
* **install:** stop when a COMPOSE_FILE set in the shell does not start the install's stack ([daba5bf](https://github.com/future-agi/future-agi/commit/daba5bf89f87c6d1eed1299ca358db527d87ff36))
* **knowledge-base:** give each self-hosted setup its own serving command ([d47d554](https://github.com/future-agi/future-agi/commit/d47d554abe6c1130b3098ca5d1c24d447f6aa802))
* **monitors:** skip project-less monitors instead of querying project_id = 'None' ([a39fe8d](https://github.com/future-agi/future-agi/commit/a39fe8dc4ae15287df8c984d6994e03698e556e9))
* **observe:** answer Users filters on native span dimensions [agent] ([a4c11e1](https://github.com/future-agi/future-agi/commit/a4c11e170edd4f1501e8b35c83b763821c6a3760))
* **observe:** latency charts show the mean on every path ([2d089b9](https://github.com/future-agi/future-agi/commit/2d089b920a2fef9f40d96ff59a08dc7e5adbbf78))
* restore the dev e2e suite after [#3065](https://github.com/future-agi/future-agi/issues/3065) (int2 CDC column, EvalsTabView locator, grid row race) ([7392ee1](https://github.com/future-agi/future-agi/commit/7392ee173e1f3be33d3951fc99b733ee3f22a04c))
* self-hosted bugs from end-to-end verification (workspace create and dates, SDK snippets, KB status, code eval model, custom model URL, login lockout, session list 500, dataset grid, secrets in logs, alerts, gateway edit, zero token cost) ([ffc96ac](https://github.com/future-agi/future-agi/commit/ffc96acbabeeeb69b4b27dc5683e1715e60ae66b))
* **serving:** one way to turn model serving on, in every message ([47a8fd4](https://github.com/future-agi/future-agi/commit/47a8fd48e089c8fa4b007b5c42d89e45623bac0b))
* **simulate:** address review on the persona and scenario-count fixes ([29d4359](https://github.com/future-agi/future-agi/commit/29d43592d38e2fc965373ab67b0adaa84ad7d391))
* **simulate:** address review on the RL environment UX fixes ([d2c8569](https://github.com/future-agi/future-agi/commit/d2c85696397e2a542cba7246f68e5b29474348f8))
* **simulate:** align dev runner LiveKit version ([202b4d6](https://github.com/future-agi/future-agi/commit/202b4d616f3ac063e4feb838de4d9f200c3f93c1))
* **simulate:** align dev runner LiveKit version ([2a401ca](https://github.com/future-agi/future-agi/commit/2a401cab52b0db9a345b4244e1dd656329ba7b29))
* **simulate:** align scenarios toolbar controls to one height ([cb7ce49](https://github.com/future-agi/future-agi/commit/cb7ce495d03a469c9a17eb658bce4f90db414d62))
* **simulate:** close the calls table with a bottom border ([b98e5f8](https://github.com/future-agi/future-agi/commit/b98e5f8a1ec99ee34745c3daf118273c9cc03f01))
* **simulate:** count scenarios from runs and the environment, not version mocks ([3d89232](https://github.com/future-agi/future-agi/commit/3d892329d2f75eb768bf09d8b6f8c846836d8c36))
* **simulate:** explain empty eval cells and show loading while a call runs [TH-8116] ([9114d18](https://github.com/future-agi/future-agi/commit/9114d18c40efddbcc53f73537a7eb98235a38080))
* **simulate:** explain empty eval cells and show loading while a call runs [TH-8116] ([6c893a3](https://github.com/future-agi/future-agi/commit/6c893a34215f7f07e82b014ba9618e34addfdc61))
* **simulate:** fence chat checkpoint publication ([1d9317a](https://github.com/future-agi/future-agi/commit/1d9317a629077c5fbf9a9a4fb07a56421b8af1c1))
* **simulate:** gate scenario job flag by runtime policy ([24708ef](https://github.com/future-agi/future-agi/commit/24708efe0191d742b908b4df26b498aba2747eac))
* **simulate:** hide Checklist, Graph and Fix with Falcon in environment call drawers ([cbde96b](https://github.com/future-agi/future-agi/commit/cbde96b1530d26770d1a7fb7b4ab42672ecc7b4e))
* **simulate:** hide Checklist, Graph and Fix with Falcon in environment call drawers ([a3004ed](https://github.com/future-agi/future-agi/commit/a3004ed267aa1f44bc2c588a71eaf1b3650e4f2e))
* **simulate:** let persona values wrap in the run's Test runs table [TH-8197] ([2979283](https://github.com/future-agi/future-agi/commit/2979283f90fa8fe01fc80430693ae208687a6fca))
* **simulate:** make run Analytics chart tooltips readable in both themes [TH-8200] ([a37c3b3](https://github.com/future-agi/future-agi/commit/a37c3b39dead732aeebed48ea1f81d1f67ce6ec2))
* **simulate:** make run Analytics chart tooltips readable in both themes [TH-8200] ([b668211](https://github.com/future-agi/future-agi/commit/b6682115ae705c325e222017c832642e9230e6cc))
* **simulate:** make the country code picker searchable ([a64db41](https://github.com/future-agi/future-agi/commit/a64db41c1bb4eb595f046a69eedada8904c8edfb))
* **simulate:** persona in the call drawer and runs table, scenario count in the version bar [TH-8180, TH-8197] ([55232b3](https://github.com/future-agi/future-agi/commit/55232b3dce9cad25bea4fc54f9f7dc03d7396ee7))
* **simulate:** place direct caller interjections at event time ([03438f8](https://github.com/future-agi/future-agi/commit/03438f864aff683d2a3190feb00c4bc00be7b2f9))
* **simulate:** preserve guest POC PIN env compatibility ([e3c5f62](https://github.com/future-agi/future-agi/commit/e3c5f62159d0eb6c704a577715381e85e9627f94))
* **simulate:** publish chat checkpoints to their environment ([fb34319](https://github.com/future-agi/future-agi/commit/fb34319e5c781b0abd03de7b2d088291477738f0))
* **simulate:** publish chat checkpoints to their environment ([28ba613](https://github.com/future-agi/future-agi/commit/28ba61339fe646898df0b5167e96d119a5536fd8))
* **simulate:** regenerate MCP manifest; update trace reader test for base_time ([fead5bf](https://github.com/future-agi/future-agi/commit/fead5bffa9e6179245cc9cda5a6ac0efa2a38405))
* **simulate:** remove customer-named PIN compatibility aliases ([74dd221](https://github.com/future-agi/future-agi/commit/74dd2211bf9cc07796b37ef1fda112965be9f9f5))
* **simulate:** resizable scenario table columns and row height ([b1dc194](https://github.com/future-agi/future-agi/commit/b1dc194e86d8316568139585fd37af8199f18168))
* **simulate:** RL environment UX fixes (country search, scenario counts, resizable table, toolbar alignment) ([68350db](https://github.com/future-agi/future-agi/commit/68350db42e3632b51879ae8b2963db5f30ff2f50))
* **simulate:** scope guest PIN policy by phone only ([0b049b8](https://github.com/future-agi/future-agi/commit/0b049b80b827f6f3ef6ae1fead4ea30478f3babe))
* **simulate:** scope PIN policy to target phone flows ([b474a1d](https://github.com/future-agi/future-agi/commit/b474a1d63fe6953c68900abdf88a5217aa259530))
* **simulate:** scope private guest PIN policy and clean stale docs ([07352db](https://github.com/future-agi/future-agi/commit/07352dbaedb5d786752be3c9b544ebf81457f732))
* **simulate:** scope private guest PIN policy to approved org ([1f776ea](https://github.com/future-agi/future-agi/commit/1f776ea69b5372548f6330be6eadb772cd93fb54))
* **simulate:** score choice-scored eval results in run groups, rows and analytics ([6d0de07](https://github.com/future-agi/future-agi/commit/6d0de0751d1c7ea998b7118bbce009c9bc898d0a))
* **simulate:** score choice-scored eval results in run groups, rows and analytics [TH-8134] ([4b540cf](https://github.com/future-agi/future-agi/commit/4b540cfe1ac932863c0687beff09a5b130c666f4))
* **simulate:** show sub-goal text in the scenarios list view [TH-8136] ([0f3a1d4](https://github.com/future-agi/future-agi/commit/0f3a1d4492a606d6ab05accaaff53a4568b672d0))
* **simulate:** show sub-goal text in the scenarios list view [TH-8136] ([bf99149](https://github.com/future-agi/future-agi/commit/bf991493b3134e316c76fd43b42afeb8e5fe3757))
* **simulate:** show the persona in the call drawer's Scenario tab [TH-8180] ([9f2e795](https://github.com/future-agi/future-agi/commit/9f2e7956baf22f75b0d346993fac57a9ed5eea14))
* **simulate:** show the run header's Completed chip in green [TH-8117] ([af54497](https://github.com/future-agi/future-agi/commit/af544970d22bb783a7793fa5f6a9540a8fda7d90))
* **simulate:** show the run header's Completed chip in green [TH-8117] ([e217f0a](https://github.com/future-agi/future-agi/commit/e217f0a10ed22d6a6f2dfbef21367a5884b59227))
* **simulate:** show the scenario count in the environment version bar ([4006b3d](https://github.com/future-agi/future-agi/commit/4006b3d8de132cd46fb8202ab9c7fbf128367b1d))
* **simulate:** show tool calls at their turn in the call transcript ([b726776](https://github.com/future-agi/future-agi/commit/b7267767a5c34fbb64825fd03a20cb93c59641ce))
* **simulate:** show tool calls at their turn in the call transcript ([dc5fd79](https://github.com/future-agi/future-agi/commit/dc5fd793d79b22809e5c41b636d73052c300c1e3))
* **simulate:** stop login autofill on hosted-platform agent fields ([f945eac](https://github.com/future-agi/future-agi/commit/f945eac0dc007ffb461207fa7c8b957af532eb01))
* **simulate:** stop login autofill on hosted-platform agent fields ([2e11d60](https://github.com/future-agi/future-agi/commit/2e11d608af79282ac9358e0a863a264e6fc804f3))
* **simulation:** retain established transcript timing fallback ([8a1ae84](https://github.com/future-agi/future-agi/commit/8a1ae84b3c95ee88a8cfb29feb4f379b28c02117))
* **storage:** send object storage through HTTP(S)_PROXY, honouring NO_PROXY ([19cbf73](https://github.com/future-agi/future-agi/commit/19cbf7319cbd425bb0ae7cf81177e2ca1be972ed))
* **temporal:** cap the embedded worker's retry exponent before evaluating it ([4fbf85e](https://github.com/future-agi/future-agi/commit/4fbf85e1656c34d08fb84f011883d03cc872ae8e))
* **tracer:** cap the voice call list's statement timeout at the builder cap ([6e97b79](https://github.com/future-agi/future-agi/commit/6e97b79287aa17d2f7bbafae566a28b9525960f3))
* **tracer:** cap the voice call list's statement timeout at the builder cap ([81a8a8e](https://github.com/future-agi/future-agi/commit/81a8a8eb472c7cbf090f7f19c0fd8680db0ef5eb))
* **tracer:** split pasted UUID lists in text in/not_in filters ([c0e2e63](https://github.com/future-agi/future-agi/commit/c0e2e6319aa05aa88c6598268b9a6295b860838e))

## [1.42.0](https://github.com/future-agi/future-agi/compare/v1.41.3...v1.42.0) (2026-09-29)


### Features

* **evals:** add 10 voice-agent system evals and use-case filter chips ([a21afb4](https://github.com/future-agi/future-agi/commit/a21afb4c96e996019adc722633c212230c690967))
* **evals:** add 10 voice-agent system evals and use-case filter chips [agent] ([c0487b4](https://github.com/future-agi/future-agi/commit/c0487b4696fb05a814711ae61a154ad0bf5e0ba4))
* **evals:** list the ten voice-agent evals in the catalog so RL environments offer them ([69561eb](https://github.com/future-agi/future-agi/commit/69561ebcb71e35535026c45a4c241a7818190f3b))
* **simulate:** 62-clip background noise catalogue and editor choices for every place ([080eba4](https://github.com/future-agi/future-agi/commit/080eba4acb61a28bf782fcef919df6f5b60ea437))
* **simulate:** add a call status column to the run trace table ([53439aa](https://github.com/future-agi/future-agi/commit/53439aafbf65fbfe78ec1833ec71267fde24aed1))
* **simulate:** environment v3 ([aa434ab](https://github.com/future-agi/future-agi/commit/aa434ab38a6523d88d162e6e57d6683d8fa4daac))
* **simulate:** integrate Omega debug failures with environment v3 ([5c3e7ec](https://github.com/future-agi/future-agi/commit/5c3e7ec824f8093281823f54db6541eba96c9347))


### Bug Fixes

* dev QA failures across tracing, datasets and eval tasks ([22b6bbd](https://github.com/future-agi/future-agi/commit/22b6bbded5130c36d2dcd808c143c6e44b38b2da))
* **evals:** address review on tags, eval overlap, consent inputs and seed version ([fbc6059](https://github.com/future-agi/future-agi/commit/fbc60597c602129513d956039c8e224979c1a291))
* **evals:** check proactive disclosure, time knowledge evidence, flag missing tool events ([8eb873e](https://github.com/future-agi/future-agi/commit/8eb873e97982292c9c5cf7c028c7db9d66f3ab44))
* **evals:** consent reads tool calls from the transcript; disclosure pass respects policy ([db19703](https://github.com/future-agi/future-agi/commit/db197033487489e35911aee09d0a0dfcb42da1ed))
* **evals:** drop the interruption-eval pointer from conversational_naturalness ([f72dad4](https://github.com/future-agi/future-agi/commit/f72dad4bada134ea6b94a6b8d7dce6e7bc5afd07))
* **evals:** prompt-governed AI disclosure, drop verification_result, Audio instead of Voice ([901aedb](https://github.com/future-agi/future-agi/commit/901aedbbeea4002124ed23e458c7f0d1abf9ecfe))
* **evals:** rebase onto environment-v3, optional consent transcript, consistent naturalness threshold ([1368bf9](https://github.com/future-agi/future-agi/commit/1368bf93bd0b2b76e9ce33e242269d1b1eb51593))
* **evals:** say the added-evaluations empty state covers only this page ([0e07aa1](https://github.com/future-agi/future-agi/commit/0e07aa184e0119dcb4e5c2b7feaae11a5d8ee31c))
* **evals:** tighten identity, consent and jailbreak judge rules ([7b7e759](https://github.com/future-agi/future-agi/commit/7b7e759a14749de5fdf4353286749133692bc0f5))
* **evals:** unclear_audio_handling judges only audible behaviour ([3c89d1d](https://github.com/future-agi/future-agi/commit/3c89d1dd8312f981db797434a3c8e8c8044b02bc))
* **evals:** unclear_audio_handling takes call audio only ([1ee1c79](https://github.com/future-agi/future-agi/commit/1ee1c79b17293d2ba09b66bb98674b3df310c2d7))
* **simulate:** align the simulation-runner LiveKit pin with ALK ([b95b4f0](https://github.com/future-agi/future-agi/commit/b95b4f05db9067eba739a2d4e66b72983816ece2))
* **simulate:** align the simulation-runner LiveKit pin with ALK ([684e701](https://github.com/future-agi/future-agi/commit/684e7019ed3ab525173dd5b8ba43874ea5e18464))
* **simulate:** count a group's completed calls only once all are loaded ([c985214](https://github.com/future-agi/future-agi/commit/c9852146c1fd32dace3f2ea16ea5b4c4ab4a97ee))
* **simulate:** disable the run header actions while the run is live ([a71cad9](https://github.com/future-agi/future-agi/commit/a71cad966555d306b4f883dc578942c5a055fe72))
* **simulate:** draw only the first five evals on the runs graph by default ([4f7afeb](https://github.com/future-agi/future-agi/commit/4f7afeb62ef82f805af5d2c4ac20b9e9a045c800))
* **simulate:** draw only the first five evals on the runs graph by default [TH-8111] ([4cb3474](https://github.com/future-agi/future-agi/commit/4cb34748c37f09541307fdd35492ee73d8570ef8))
* **simulate:** drop the repeats popup and disable run actions while live [TH-8112] [TH-8115] ([cd55a48](https://github.com/future-agi/future-agi/commit/cd55a486973bbc5181ef14fb8ff65e74f222319e))
* **simulate:** fail chat at once when a run's saved files are gone ([e565c9b](https://github.com/future-agi/future-agi/commit/e565c9be0d1aa8e2c2ae98c68f92587eecaa8527))
* **simulate:** fail chat only when a run's authoring archive is gone ([a6c68ba](https://github.com/future-agi/future-agi/commit/a6c68bae247ccb5fada9062db6e1635bccdb0888))
* **simulate:** fill the Run preflight button with the theme's primary ([5b2ae3d](https://github.com/future-agi/future-agi/commit/5b2ae3dafe59fc5fa6eeaaec50ce50e5e3498323))
* **simulate:** fill the Run preflight button with the theme's primary [TH-8109] ([d48023b](https://github.com/future-agi/future-agi/commit/d48023bb02de22c75dc5649a1f97bf06db218b97))
* **simulate:** keep dropped scenarios a selected run's calls point at ([712440e](https://github.com/future-agi/future-agi/commit/712440e60048a8871a9289bc08d9f100b0e52b72))
* **simulate:** keep dropped scenarios with calls visible so run history still finds them ([07a63cb](https://github.com/future-agi/future-agi/commit/07a63cb5f72d1cf6583d769b548219dd2850cad6))
* **simulate:** leave Runs out of the building tab rail ([f5c39c0](https://github.com/future-agi/future-agi/commit/f5c39c03292e63e0b3fdd10f5554e1a798c88370))
* **simulate:** let people add any eval to a harness environment and map every input ([0628799](https://github.com/future-agi/future-agi/commit/0628799da892ee6dfa14d04c4fbd6c0a57a5d87f))
* **simulate:** map only the call statuses a hosted run sets ([4a93577](https://github.com/future-agi/future-agi/commit/4a93577ec6637b99a49bcbc15288ac3714ecf26d))
* **simulate:** name Vapi and Retell environments by the provider's agent name ([1f34ecf](https://github.com/future-agi/future-agi/commit/1f34ecfe79b1f7c00416169b928e19be00390597))
* **simulate:** name Vapi and Retell environments by the provider's agent name ([682f22c](https://github.com/future-agi/future-agi/commit/682f22c825cf2252bee9eeffa728c0d26a7b1d51))
* **simulate:** refuse a scenario edit the archive cannot take, and hide dropped scenarios that have calls ([d4dafd6](https://github.com/future-agi/future-agi/commit/d4dafd64e308e5bebef7e5f77263848256c42926))
* **simulate:** return the common error envelope from the debug-analysis 409s ([31fbc78](https://github.com/future-agi/future-agi/commit/31fbc78917343dd37bcf94e620e439c6c23fd517))
* **simulate:** return the common error envelope from the debug-analysis 409s ([9c25217](https://github.com/future-agi/future-agi/commit/9c2521718c739181b52ebb5065b7c3aa0c290d9b))
* **simulate:** run straight from the header Run, drop the repeats popup ([a12f0ec](https://github.com/future-agi/future-agi/commit/a12f0ec4396e04a59644f06bb41a29b2c720ef83))
* **simulate:** scenario edits keep every scenario's files and reach the next run ([9874168](https://github.com/future-agi/future-agi/commit/9874168c32b4db1f318bb48d9b155d8c705a490a))
* **simulate:** scenario edits keep every scenario's files and reach the next run ([fbc939b](https://github.com/future-agi/future-agi/commit/fbc939bc72831f7a336f78f02c5c5bc5fc993cfd))
* **simulate:** scenario edits resolve older suites and re-check only the scenarios they change ([19a8366](https://github.com/future-agi/future-agi/commit/19a836616c55c4aa2c3a62626c1e07519e54158c))
* **simulate:** show a spinner while the Runs tab loads, render the summary directly ([0bfd5fc](https://github.com/future-agi/future-agi/commit/0bfd5fc30a9827160806ce3bc1dc1fc845df5602))
* **simulate:** show the run header's Completed chip in green ([5aec2aa](https://github.com/future-agi/future-agi/commit/5aec2aae6edef2087628a4732fa9ef014939a444))
* **simulate:** show the Runs tab only after a run, add a call status column [TH-8110] ([e62e50b](https://github.com/future-agi/future-agi/commit/e62e50bad59537b62931580babd3223ac70fb5cc))
* **simulate:** show the Runs tab only once a run exists ([29deedd](https://github.com/future-agi/future-agi/commit/29deedd69778f390d08a8bb55c09fc376d59a3bd))
* **simulation:** store full-length Omega requirement IDs ([8f0f787](https://github.com/future-agi/future-agi/commit/8f0f7876b97e94865871863a3d9b074c6a403844))
* **simulation:** support 256-character Omega requirement IDs ([869742b](https://github.com/future-agi/future-agi/commit/869742bd2a8258df1b933c2ba10e470ecb977704))
* **tracer:** treat a sub-microsecond Temporal deadline as expired ([e4812d0](https://github.com/future-agi/future-agi/commit/e4812d06c7d68b3e10c916a47e52cc80c46df545))
* **voice:** the background selector parses the catalogue's named clip ids ([846826e](https://github.com/future-agi/future-agi/commit/846826ef345bd6690179afebef298949b898fa8c))

## [1.41.3](https://github.com/future-agi/future-agi/compare/v1.41.2...v1.41.3) (2026-09-28)


### Bug Fixes

* **observe:** support 60-second GCP interactive reads ([f271d0d](https://github.com/future-agi/future-agi/commit/f271d0dd6fede6c002fce1f5e6ad068e8cade62e))
* **observe:** support 60-second GCP read budgets [agent] ([0f9492c](https://github.com/future-agi/future-agi/commit/0f9492c3a1223534018e4201f1c50c5cf7f88639))

## [1.41.2](https://github.com/future-agi/future-agi/compare/v1.41.1...v1.41.2) (2026-09-28)


### Bug Fixes

* **ci:** build the E2E backend image from source on every PR ([1301d21](https://github.com/future-agi/future-agi/commit/1301d218e7e9ce5bb16c84c84df1582241d195e2))
* **ci:** build the E2E backend image from source on every PR ([b628952](https://github.com/future-agi/future-agi/commit/b62895206954a10b073efc6dada8decddea1cf03))
* **evals:** make the code executor's local fallback explicit ([055bd45](https://github.com/future-agi/future-agi/commit/055bd45eae2ef2320f6a98823bf502ed622380e5))
* **model-hub:** bind each eval template choice as its text in choice stats ([9727892](https://github.com/future-agi/future-agi/commit/972789214847c99df4aa2c1d453a7c98714c55a5))
* **model-hub:** pass eval template choices to the choice-stats query as a parameter ([be81b5d](https://github.com/future-agi/future-agi/commit/be81b5d13dcdad7d9ee36667b9b4b5e21bf5e29c))
* **model-hub:** validate eval usage sort column against the grid's columns ([57c7d84](https://github.com/future-agi/future-agi/commit/57c7d846dea0073baa2d262b552f617946c1df02))
* **model-hub:** validate the eval usage sort column and bind choice-stats choices ([0c8db34](https://github.com/future-agi/future-agi/commit/0c8db34a9ebfcfcd9e12ead86665b9aa716e9b28))
* **observe:** grid refresh during load, Users grid never blank, drawer shortcuts only when open ([f7758a1](https://github.com/future-agi/future-agi/commit/f7758a122032c65d5f5d401088994120cde450bf))
* **observe:** retry a Trace/Span block a cancelled refresh left failed ([dbe9303](https://github.com/future-agi/future-agi/commit/dbe930362e0af4cf791796da2a2a291c2f4ca65b))
* **security:** stop granting anonymous write, delete and list on the MinIO bucket ([c4d0857](https://github.com/future-agi/future-agi/commit/c4d08575bfb4c30811dd4a754929aaaa1994e475))
* **security:** stop self-hosted installs sending the org API key to futureagi.com ([6474155](https://github.com/future-agi/future-agi/commit/647415548292b9507ecc827779aa6c0b467c54c0))
* **security:** stop self-hosted installs sending the org API key to futureagi.com ([905d79d](https://github.com/future-agi/future-agi/commit/905d79da488b79c1f75be0b1746cc8aff0b70f48))
* **self-host:** keep peerdb-minio in the default compose profile ([f42163a](https://github.com/future-agi/future-agi/commit/f42163a2270261c5833f5054593289ff21b299c4))
* **storage:** stop granting anonymous write, delete and list on the MinIO bucket ([d77096b](https://github.com/future-agi/future-agi/commit/d77096b0231cc47636b9718285e00c1e8235265d))
* **trace-drawer:** Esc closes the trace drawer, not the full-page trace view ([8aa5c3a](https://github.com/future-agi/future-agi/commit/8aa5c3af0c488a91739e52f4f19f8ada08be5009))
* **trace-drawer:** scope J/K/Esc shortcuts to the open trace drawer ([837c6e8](https://github.com/future-agi/future-agi/commit/837c6e8628d1c302918728eedc653bad2376d95d))
* **users:** a paused first page no longer brings back the empty screen ([255903f](https://github.com/future-agi/future-agi/commit/255903f9c6492c5e6e03b6e9b3693b14473e51f3))
* **users:** show the Users grid in every state that is not confirmed empty ([de9984d](https://github.com/future-agi/future-agi/commit/de9984d4329d343f05384fa566019b32ac8e3261))

## [1.41.1](https://github.com/future-agi/future-agi/compare/v1.41.0...v1.41.1) (2026-09-25)


### Bug Fixes

* **frontend:** point get-started experiment links at develop and drop unused route imports ([8fd831e](https://github.com/future-agi/future-agi/commit/8fd831e8b2e3febe1f67489c65850a2d9ef7cf51))
* **frontend:** retire the prototype routes and repoint get-started links ([e7112f2](https://github.com/future-agi/future-agi/commit/e7112f23f8b4354bb0dd6df854e9a041d425550c))
* **oss:** explain failed pre-flight checks and keep the launch moving ([5d11782](https://github.com/future-agi/future-agi/commit/5d117826660b3e3a1947fdcbc5156b8cc98e9733))
* **oss:** explain failed pre-flight checks and keep the launch moving ([83a7e63](https://github.com/future-agi/future-agi/commit/83a7e631a580768707e0021dd259d3f8a2010b62))
* **self-host:** pin a frozen build of the last community MinIO release ([43026bd](https://github.com/future-agi/future-agi/commit/43026bd5d49dc26d1a7c192d198f394f6057203f))
* **self-host:** pin a frozen build of the last community MinIO release ([871165b](https://github.com/future-agi/future-agi/commit/871165bbf79c61d6cee7377ba65b3b2d513f1096))

## [1.41.0](https://github.com/future-agi/future-agi/compare/v1.40.1...v1.41.0) (2026-09-25)


### Features

* **error-feed:** include source names in worker claims ([7e3f852](https://github.com/future-agi/future-agi/commit/7e3f85218873d293919ec301731df59b585335d8))
* **error-feed:** include source names in worker claims ([c660166](https://github.com/future-agi/future-agi/commit/c6601663ba3aa58b486fadde5979a80c89c0df43))


### Bug Fixes

* **tracer:** batched backfill_legacy_scans command replacing 0101 data step ([d6b96fc](https://github.com/future-agi/future-agi/commit/d6b96fc0ef3060f98836f57b12524061429e3c89))
* **tracer:** batched backfill_legacy_scans command replacing 0101 data step (main) ([a540abf](https://github.com/future-agi/future-agi/commit/a540abf87375244b0236211e356ccc6fc28d8ef7))

## [1.40.1](https://github.com/future-agi/future-agi/compare/v1.40.0...v1.40.1) (2026-09-24)


### Bug Fixes

* **install:** make the first account reachable on a fresh install ([ec9ba10](https://github.com/future-agi/future-agi/commit/ec9ba10765a7a0deb222b169689db86eb8350288))
* **install:** make the first account reachable on a fresh install ([999e70a](https://github.com/future-agi/future-agi/commit/999e70a8598de2f8593848de0733987845295f1c))
* **release:** bump US Property Catalog image tags ([e521968](https://github.com/future-agi/future-agi/commit/e52196801db5be7791d09e460a9b520aa9f39634))
* **release:** include US Property Catalog image tags in release bumps ([516624d](https://github.com/future-agi/future-agi/commit/516624d05e86141222e03c1f625299fc94a64f30))

## [1.40.0](https://github.com/future-agi/future-agi/compare/v1.39.0...v1.40.0) (2026-09-23)


### Features

* **error-feed:** announce stored roots for Omega investigations ([db45e26](https://github.com/future-agi/future-agi/commit/db45e26991bcc37028108390c56ad6b8aa6900fa))
* **error-feed:** assess grouped issue severity with evidence ([3db5580](https://github.com/future-agi/future-agi/commit/3db5580dbfb1c32ac316264660ab7e18b0f17435))
* **error-feed:** backfill legacy scans in Django migrations ([0156268](https://github.com/future-agi/future-agi/commit/0156268ecaef33b1e81cc800d9d9966964008841))
* **error-feed:** checkpoint F6 runtime for local integration testing ([138c72b](https://github.com/future-agi/future-agi/commit/138c72b6814d026ddceb1d8c1583d69f3fe28315))
* **error-feed:** cut over trace admission to Omega ([584b726](https://github.com/future-agi/future-agi/commit/584b72639fde7a40f98ec4d9b8326f65ab72379f))
* **error-feed:** enable grouping, scored evals, and causal breadcrumbs ([772013b](https://github.com/future-agi/future-agi/commit/772013b4cb3daa6e748adb0d22b7c949badef20b))
* **error-feed:** integrate Omega investigation handoff ([bb5311e](https://github.com/future-agi/future-agi/commit/bb5311eb1cc43f36a39c3813a02baa7b5565aa5f))
* **error-feed:** normalize Omega investigation reports ([a63240d](https://github.com/future-agi/future-agi/commit/a63240df846355eacfe70cbbf88d49a3f2718960))
* **error-feed:** prepare scoped grouping snapshots and feature jobs ([4872736](https://github.com/future-agi/future-agi/commit/4872736b034866551cf102d6bb51c75c21022737))
* **error-feed:** prepare scoped grouping snapshots and jobs (TH-7782) ([cf2c168](https://github.com/future-agi/future-agi/commit/cf2c16867e980b9eec717c9ceced75803bdb4ae6))
* **error-feed:** publish F6 clusters and severity to the Feed (TH-7782) ([318771e](https://github.com/future-agi/future-agi/commit/318771eaa468766bd42cb0548285f3c926061677))
* **error-feed:** read current findings in cluster RCA ([29f6f18](https://github.com/future-agi/future-agi/commit/29f6f18874172425c2eb9c32c6138e5cbe92c75e))
* **error-feed:** read normalized investigations in Feed ([6adea8d](https://github.com/future-agi/future-agi/commit/6adea8d94efb0958ab867d3a4fba99e18753ed5d))
* **error-feed:** render cited causal breadcrumbs from Omega roles ([f1c1e19](https://github.com/future-agi/future-agi/commit/f1c1e19dec77b05c5581b077aea3cbba5bd4a1a5))
* **error-feed:** retain tenant-scoped usage receipts ([1c4dd73](https://github.com/future-agi/future-agi/commit/1c4dd73f25ab5cc5cf4a8106026b201a50145395))
* **error-feed:** schedule local Omega processing on the existing stack ([987f4ed](https://github.com/future-agi/future-agi/commit/987f4edb97b759a3e9a01feebde01aef745a1d31))
* **error-feed:** wire durable Omega investigations and grouping ([8343400](https://github.com/future-agi/future-agi/commit/83434007bb749023f709b14abad269dfff80f2bd))
* **release:** gate deployment bumps on verified Error Feed worker images ([508b0c5](https://github.com/future-agi/future-agi/commit/508b0c555126a08668840e6de2e0159679568aaf))
* **release:** gate deployments on verified Error Feed worker images ([5e818f2](https://github.com/future-agi/future-agi/commit/5e818f2e4f98c2c5cbe737b000be87495756c4e9))


### Bug Fixes

* **error-feed:** align causal breadcrumb tests and Feed schema ([fdd83d2](https://github.com/future-agi/future-agi/commit/fdd83d284b7cdc8e04493b205f45ef150fc31d71))
* **error-feed:** align investigation errors with main API envelope ([e164a5a](https://github.com/future-agi/future-agi/commit/e164a5a5591fbf1e449108fd91dad08c0dc5bef4))
* **error-feed:** align runtime contracts and stack test boundaries ([4c1ba76](https://github.com/future-agi/future-agi/commit/4c1ba76ce005fe1688694d12e81a35c4875deaeb))
* **error-feed:** avoid revision churn on repeated severity edits ([746453b](https://github.com/future-agi/future-agi/commit/746453b7a65d72af53856b43eea8ebfe15c69973))
* **error-feed:** derive trace status from resolved outcome ([2cb8dc6](https://github.com/future-agi/future-agi/commit/2cb8dc6460657428856d4bb62c1dce7375e97485))
* **error-feed:** disable legacy scanner in v2 stack ([3cf494f](https://github.com/future-agi/future-agi/commit/3cf494f34d427fe2a929fef635f10794e47c8562))
* **error-feed:** dispatch severity accounting endpoints safely ([6577d5a](https://github.com/future-agi/future-agi/commit/6577d5a9ae23dce69ecdc380ef9efb5cfe9de22f))
* **error-feed:** enable Omega grouping by default ([60321d7](https://github.com/future-agi/future-agi/commit/60321d72b315ebc8acf76ca47feb52f0d7d5c4b2))
* **error-feed:** fence grouping cohort claims ([3b6c0c1](https://github.com/future-agi/future-agi/commit/3b6c0c16cb1a4038233e54cc958c919e4394a691))
* **error-feed:** fence superseded attempts and inconclusive reports ([a225d21](https://github.com/future-agi/future-agi/commit/a225d21915a6ffbb135527443d7d47eb87cb9dcc))
* **error-feed:** gate grouping on budgets and EE deployment ([7882a12](https://github.com/future-agi/future-agi/commit/7882a12a7a800fc63489922a022edf32a3a6d90f))
* **error-feed:** group only unresolved task failures ([c7f0c48](https://github.com/future-agi/future-agi/commit/c7f0c48bc2c089483bcf0a09ab2237e1657ffbe5))
* **error-feed:** honor v2 sampling and stable delivery identity ([1b81dc1](https://github.com/future-agi/future-agi/commit/1b81dc1865027591ff43c0444031fdb2c3c43314))
* **error-feed:** keep collector ingest available without Kafka ([a23f3c9](https://github.com/future-agi/future-agi/commit/a23f3c97645d3f1eed19db5af780be450864d2d1))
* **error-feed:** keep issue details and receipts scannable ([55e8684](https://github.com/future-agi/future-agi/commit/55e86846e42479dea5cefee9c0dea0a51f82ca8f))
* **error-feed:** keep repeated severity edits idempotent (TH-7782) ([11e6389](https://github.com/future-agi/future-agi/commit/11e6389854fad3059221a27d883ca305afd81495))
* **error-feed:** keep scanner issue IDs compact ([2822c48](https://github.com/future-agi/future-agi/commit/2822c487fcbb228ddf2553631a6d98b266df6156))
* **error-feed:** measure native token usage and configure budget enforcement ([1bf063b](https://github.com/future-agi/future-agi/commit/1bf063b2fd5e9f1d68f7b44f38ce0ed3f192e152))
* **error-feed:** merge tracer migration heads ([437153d](https://github.com/future-agi/future-agi/commit/437153d71899bab74b6b781fca757aa343174d6a))
* **error-feed:** omit legacy sweep from v2 schedules ([13da1a4](https://github.com/future-agi/future-agi/commit/13da1a46879072927a54eb9ff395d79e45e9207c))
* **error-feed:** order feature jobs after merged tracer migrations ([ddd30fa](https://github.com/future-agi/future-agi/commit/ddd30fa443d72c9c5dae4748a91ddb558b9e028a))
* **error-feed:** publish concise F6 issue titles ([cc3e87c](https://github.com/future-agi/future-agi/commit/cc3e87c2fc0ddc2db8410271c6fa087cb7fd1fb3))
* **error-feed:** publish concise F6 issue titles ([7d69abd](https://github.com/future-agi/future-agi/commit/7d69abd2f2a0fb4fc1472e45d2b5e823200a6d97))
* **error-feed:** publish evidence-backed fix layer assessments ([2a15325](https://github.com/future-agi/future-agi/commit/2a15325e2e67357cc97e8fa8ae74bedefb6c8df1))
* **error-feed:** publish evidence-backed fix layer assessments ([aa75620](https://github.com/future-agi/future-agi/commit/aa75620a6fbdbe23952a431c472502e2857f7013))
* **error-feed:** route v2 sampling configs to Omega ([3a95528](https://github.com/future-agi/future-agi/commit/3a95528b90364b442324c23da2d046a89a4cbb90))
* **error-feed:** show trace graph load failures explicitly ([c399820](https://github.com/future-agi/future-agi/commit/c399820a8152f8521861fe5b30f6b3996097f11e))
* **error-feed:** show trace graph load failures explicitly ([9df652e](https://github.com/future-agi/future-agi/commit/9df652e0c63c7f7e8da3e9ec2e6f8ff0b8eb6cfc))
* **error-feed:** standardize grouping and severity error envelopes ([ba0f968](https://github.com/future-agi/future-agi/commit/ba0f968380f2619e4a817ec5284f0b5c08a5597d))
* **error-feed:** use scanner IDs for Omega clusters ([86c7216](https://github.com/future-agi/future-agi/commit/86c721641dded952d958d2cb784ada22f44880ff))
* **error-feed:** verify integration against the existing local stack ([bdd2c7b](https://github.com/future-agi/future-agi/commit/bdd2c7ba0a80eed9641b42dd575e365b16ff677a))
* **grouping:** align checkpoint bound with worker transport ([2bd08b3](https://github.com/future-agi/future-agi/commit/2bd08b34ef97eabc900f32a485c74a3426f96228))
* **grouping:** align checkpoint transport bound ([370874c](https://github.com/future-agi/future-agi/commit/370874c4cf6feccdb97fdeb3797f180f61d4f5ec))
* **TH-7782:** use organization lookup for release app token ([5dd891f](https://github.com/future-agi/future-agi/commit/5dd891f553627b88c85c82d09c565d861b091299))
* **TH-7782:** use organization lookup for release app token ([72564f4](https://github.com/future-agi/future-agi/commit/72564f4732935e6e013abf4b79db008e4a5f944e))
* **tracer:** project Omega issue findings into cited reel steps ([63cfefc](https://github.com/future-agi/future-agi/commit/63cfefcb0413b1e7afaad7b8bfd91ac0ccbc4913))
* **tracing:** cluster choice-scored eval failures ([be6a162](https://github.com/future-agi/future-agi/commit/be6a1621f800ed790f45c279642de0574456682e))
* **tracing:** honor eval score thresholds ([f9871c2](https://github.com/future-agi/future-agi/commit/f9871c2b8111007d0d5e363bcdadc1cd4db40c7a))
* **tracing:** ignore deleted eval memberships ([f592153](https://github.com/future-agi/future-agi/commit/f5921537e4c4278cff7149c502bcef591a923c4d))
* **tracing:** resolve structured eval scores safely ([533397a](https://github.com/future-agi/future-agi/commit/533397a167063d1b264e4365bd46fc9835f5a3df))

## [1.39.0](https://github.com/future-agi/future-agi/compare/v1.38.4...v1.39.0) (2026-09-21)


### Features

* **agentcc:** add Vertex provider credentials and deployment guide ([c2ce659](https://github.com/future-agi/future-agi/commit/c2ce65962140895baa051508dc2cd608ba9735fc))
* **agentcc:** filter request logs and group analytics by application and service ([fd08c00](https://github.com/future-agi/future-agi/commit/fd08c0024bac0f604df86bf4fa1c2be6fddc1883))
* **agentcc:** filter request logs and group analytics by application and service (TH-8004) ([a98dffd](https://github.com/future-agi/future-agi/commit/a98dffd3c14433a245b4775467c27319b570ddcc))
* **agentcc:** route Claude Agent SDK requests to Vertex Gemini ([f9d6c50](https://github.com/future-agi/future-agi/commit/f9d6c5035e03bc2bf695201ff7603ccb1e4eab1f))
* **agentcc:** route Claude-compatible requests to Vertex Gemini ([0622851](https://github.com/future-agi/future-agi/commit/062285197d50b39be164762bc3180da57ad7c194))


### Bug Fixes

* **agentcc:** keep a caller's Other application out of the folded tail ([b8440e5](https://github.com/future-agi/future-agi/commit/b8440e5d76314f8e42005326e4eff447fca4e3b9))
* **agentcc:** preserve schema properties and forward resolved aliases ([99ce18b](https://github.com/future-agi/future-agi/commit/99ce18bc106326d67d37803b9df794e74f0a2e1c))
* point every Discord link at the one working invite ([96f12d1](https://github.com/future-agi/future-agi/commit/96f12d1e227bde75d6d7a12214e48da19c0a5081))
* point every Discord link at the one working invite ([d02df8d](https://github.com/future-agi/future-agi/commit/d02df8d96f564253344ed4849435340edd9c52c6))
* **tests:** stabilize backend CI failure groups ([270a146](https://github.com/future-agi/future-agi/commit/270a1460ce21b560d09e5ce91fdea91c4ff04490))

## [1.38.4](https://github.com/future-agi/future-agi/compare/v1.38.3...v1.38.4) (2026-09-17)


### Bug Fixes

* **eval-tasks:** gate errored/skipped requeue on terminal watermark; sniff URL type across chunks ([b40979c](https://github.com/future-agi/future-agi/commit/b40979c80e8428f853ebfa5538383aa83964edb5))
* **eval-tasks:** stop continuous tasks re-running converged failures every poll ([a113af7](https://github.com/future-agi/future-agi/commit/a113af7aa78c1fb8cf8e6aa19f7f983a28b51f52))
* **eval-tasks:** stop continuous tasks re-running converged failures every poll ([7c99959](https://github.com/future-agi/future-agi/commit/7c99959f98669372f8611764e83d09df4683d110))

## [1.38.3](https://github.com/future-agi/future-agi/compare/v1.38.2...v1.38.3) (2026-09-15)


### Bug Fixes

* **eval-tasks:** batch changed span identities below query size limit ([cea0869](https://github.com/future-agi/future-agi/commit/cea0869da412809eb89def3fbbaab7ad00a414f8))
* **eval-tasks:** bound remaining continuous candidate queries ([201282d](https://github.com/future-agi/future-agi/commit/201282d2d48417d2efca9c69da7a29393515d224))
* **eval-tasks:** hotfix dense continuous and sparse historical selection ([5d9bea6](https://github.com/future-agi/future-agi/commit/5d9bea61fb9a6cd2ca64a89810d7fb3a6c498276))
* **eval-tasks:** page dense continuous candidate windows with workflow budget ([6c27485](https://github.com/future-agi/future-agi/commit/6c274857080e651f9b285e72e75073ac1a42151c))
* **eval-tasks:** preserve historical selection window across budget escalation ([7d404f4](https://github.com/future-agi/future-agi/commit/7d404f4d889ebae295e03deef132be119d522bed))
* **tracer:** escalate small-limit eval task selection to the workflow budget ([6965153](https://github.com/future-agi/future-agi/commit/6965153ae9cd46e80c85572a078f386bb09d0931))

## [1.38.2](https://github.com/future-agi/future-agi/compare/v1.38.1...v1.38.2) (2026-09-14)


### Bug Fixes

* **dashboards:** apply Dataset and Eval Source filters to eval metric charts ([40d950d](https://github.com/future-agi/future-agi/commit/40d950d25851b1c7286d3e8ae302d33699af0e4e))
* **dashboards:** apply Dataset and Eval Source filters to eval metric charts (TH-7938) ([37d07d7](https://github.com/future-agi/future-agi/commit/37d07d766bac22afc8832854b72168b07460f440))
* **dashboards:** apply Dataset and Eval Source filters to eval metric charts (TH-7938) ([09046a8](https://github.com/future-agi/future-agi/commit/09046a89d629226f35d9dbba9e0b00dcc3fc4e4e))
* **evals:** preserve system eval binding config (TH-7897) ([2a7ced2](https://github.com/future-agi/future-agi/commit/2a7ced21559c242b6453420c549a274f8f09fb05))

## [1.38.1](https://github.com/future-agi/future-agi/compare/v1.38.0...v1.38.1) (2026-09-12)


### Bug Fixes

* **ci:** pull MinIO from quay.io; Docker Hub no longer serves minio/minio ([cfdaf41](https://github.com/future-agi/future-agi/commit/cfdaf41764ea5124358bd96ac1474d146c2a938c))
* **ci:** pull MinIO from quay.io; Docker Hub no longer serves minio/minio ([9a2b3bf](https://github.com/future-agi/future-agi/commit/9a2b3bfaf8c7ec42b7c591fab6f52daf2728f25a))

## [1.38.0](https://github.com/future-agi/future-agi/compare/v1.37.2...v1.38.0) (2026-09-11)


### Features

* **admin:** show Custom Tools to staff as read-only [TH-7898] ([6ddb301](https://github.com/future-agi/future-agi/commit/6ddb30100e4f500bb6926ed54e0cf8c3b2cc4814))
* **harness:** persist polled Daytona diagnostics ([85eb0d8](https://github.com/future-agi/future-agi/commit/85eb0d81c2ba5d3f708fe9f472116f36fe2f014a))
* **simulate:** let the guest's Observe collector be configured separately ([20c83fd](https://github.com/future-agi/future-agi/commit/20c83fdb49c4ccdb36265873cec0e723a368f213))
* **simulate:** merge Hosted Bundle V2 production flow into dev ([946fb1f](https://github.com/future-agi/future-agi/commit/946fb1f16e8b47f181442aa981830d6c6e136a16))
* **simulate:** pass Observe configuration to the harness guest and allow its collector ([2e6d8eb](https://github.com/future-agi/future-agi/commit/2e6d8eb7381c4d61bad88ef81a2f61aa0ae9c0b4))
* **simulate:** Retell outbound phone simulation on the hosted runner ([dc102c1](https://github.com/future-agi/future-agi/commit/dc102c10cbbe205ac7488340d1407791dc8aa1bb))
* **simulate:** send the harness job's tenancy context to the guest for tracing ([77dff97](https://github.com/future-agi/future-agi/commit/77dff97819bedfcf9b07c19cfd3d48d2cc21e767))


### Bug Fixes

* bound sampled eval task selection scans ([c024254](https://github.com/future-agi/future-agi/commit/c0242548361727027fa256b41d60148610f7038a))
* **contracts:** regenerate swagger for the admin invoice preview docstring ([#2721](https://github.com/future-agi/future-agi/issues/2721)) ([b2c38e8](https://github.com/future-agi/future-agi/commit/b2c38e8d64a2ff88b53bb68dcc51b34a610aa493))
* **falcon-ai:** create agent evals by default when no type is given ([bea4b4c](https://github.com/future-agi/future-agi/commit/bea4b4cf32cf2a66b4ca29138780a0b08bbe788d))
* **falcon-ai:** create agent evals by default when no type is given ([f62e77b](https://github.com/future-agi/future-agi/commit/f62e77b694aa58563af8268fc41f944514e829d8))
* **harness:** harden Daytona diagnostics capture ([643c851](https://github.com/future-agi/future-agi/commit/643c851810211c6cad6f954cac249e84b3a8c5ec))
* **harness:** normalize polled Daytona logs ([4ce6155](https://github.com/future-agi/future-agi/commit/4ce61559fbc5cf3e230e242deb2ede95688153f4))
* **harness:** redact arbitrary secret aliases ([1785a84](https://github.com/future-agi/future-agi/commit/1785a84be01099c3e47e964600deed03fef65a3d))
* **harness:** surface runtime validation cause ([b4457f3](https://github.com/future-agi/future-agi/commit/b4457f35fb85633e0d152310d532c13315dec129))
* **observe:** attributes search, project sharing, and eval-mapping UX ([#2718](https://github.com/future-agi/future-agi/issues/2718)) ([ceb8d40](https://github.com/future-agi/future-agi/commit/ceb8d401d49886712e559b9c4e3f0764f15daaea))
* **simulate:** align harness backend CI contracts ([04095c2](https://github.com/future-agi/future-agi/commit/04095c29dc1d8bb50d0c9aff45413aaeaf48e266))
* **simulate:** bound wall-clock for every hosted voice job ([186cbfa](https://github.com/future-agi/future-agi/commit/186cbfa602ab5d6bbf0abcf4e449546eb12be43e))
* **simulate:** declare resolved credential names to harness ([43be849](https://github.com/future-agi/future-agi/commit/43be84910efac548303ad2c3bfadd91d3af0494d))
* **simulate:** derive the Observe collector host only from an explicit base url ([3d089b5](https://github.com/future-agi/future-agi/commit/3d089b570cecf246f4a3a84ecff9bdcc7fc93348))
* **tests:** restore NLTK corpus initialization ([9b12fcf](https://github.com/future-agi/future-agi/commit/9b12fcf2812d9f80cf7ad927cf14d1732ac0cc30))

## [1.37.2](https://github.com/future-agi/future-agi/compare/v1.37.1...v1.37.2) (2026-09-10)


### Bug Fixes

* **marketplace:** make the consumer heartbeat, follow Google's state, and type usage per metric ([de70acf](https://github.com/future-agi/future-agi/commit/de70acfc87adf60030badfeb4864cf22b832beac))
* **marketplace:** make the consumer heartbeat, follow Google's state, and type usage per metric [TH-7731] ([ab66aea](https://github.com/future-agi/future-agi/commit/ab66aea398056db402f11489e5cbc4a9e98a9819))
* **tests:** give the marketplace reconcile subscriptions a tier ([32e5cc4](https://github.com/future-agi/future-agi/commit/32e5cc49e736c854e987facd31f47e9a60029660))

## [1.37.1](https://github.com/future-agi/future-agi/compare/v1.37.0...v1.37.1) (2026-09-09)


### Bug Fixes

* **accounts:** fix Marketplace runtime errors that escaped review ([cae3a2b](https://github.com/future-agi/future-agi/commit/cae3a2b58a39c97123fc36950ecccb97e4679a58))

## [1.37.0](https://github.com/future-agi/future-agi/compare/v1.36.1...v1.37.0) (2026-09-09)


### Features

* **accounts:** GCP Marketplace integration [TH-7731] ([c4157b7](https://github.com/future-agi/future-agi/commit/c4157b7647fd2aaaff78b544230e7eed34d388d4))


### Bug Fixes

* **accounts:** harden Marketplace failure paths [TH-7731] ([cb68f0b](https://github.com/future-agi/future-agi/commit/cb68f0b4f42dcd405b49c54e9a89e3732f5d50ad))
* **accounts:** resolve latest Marketplace review findings [TH-7731] ([b364723](https://github.com/future-agi/future-agi/commit/b364723f1aa6717941ebedd895237a92d38fa332))
* **deps:** repin restrictedpython and regenerate requirements.txt for linux/py3.11 ([c24443f](https://github.com/future-agi/future-agi/commit/c24443ff85308ba4bdd87df64eeee0786ff5eff1))
* **tracer:** apply ground truth to Observe simple evals (TH-7896) ([1f788ef](https://github.com/future-agi/future-agi/commit/1f788ef2a610a81f84916ec03c5c00af11a888ad))
* **tracer:** apply ground truth to Observe simple evals (TH-7896) ([4c0389d](https://github.com/future-agi/future-agi/commit/4c0389d9a7cba2f4410beb85d859294e99c5e175))
* **tracer:** unblock bin/test migrations and format the ground truth tests ([e21ff4a](https://github.com/future-agi/future-agi/commit/e21ff4ab3bba8cd62f79380328650e026f3d1842))

## [1.36.1](https://github.com/future-agi/future-agi/compare/v1.36.0...v1.36.1) (2026-09-08)


### Bug Fixes

* **observe:** consolidate filtering and query optimizations ([83ffe21](https://github.com/future-agi/future-agi/commit/83ffe21dbe9a692469597ebdc42e28493a271b83))

## [1.36.0](https://github.com/future-agi/future-agi/compare/v1.35.0...v1.36.0) (2026-09-08)


### Features

* **tracer:** poll Retell for voice observability instead of webhooks ([51d3edf](https://github.com/future-agi/future-agi/commit/51d3edfbec2c746a0a3e8974a995c894ceed77be))


### Bug Fixes

* **alerts:** carry the fired issue's window and filters into View Trace [TH-7792] ([aebed6c](https://github.com/future-agi/future-agi/commit/aebed6c41def527a8ac63918dc9aa488a5b298b2))
* **catalog:** recover snapshot startup and revision ordering ([#2593](https://github.com/future-agi/future-agi/issues/2593)) ([8bf5d70](https://github.com/future-agi/future-agi/commit/8bf5d7053502bb610cba51944a149bed38f67b09))
* **ci:** repair the disposable-domains refresh, and block two new domains ([#2584](https://github.com/future-agi/future-agi/issues/2584)) ([b269142](https://github.com/future-agi/future-agi/commit/b2691423ff699a7ea5167a2b167ec675f127a586))
* **gateway:** harden the provider dialog and show full session IDs ([#2595](https://github.com/future-agi/future-agi/issues/2595)) ([b5b10ec](https://github.com/future-agi/future-agi/commit/b5b10ecbbb0cdd86b2364138260650d214c48214))
* **models:** carry the region pin into the final fallback path ([ea61285](https://github.com/future-agi/future-agi/commit/ea6128560dc32c0d2397a0d801ff37c8e5b8fee5))
* **models:** pin gemini-3.5-flash to its serving region on the direct path ([874f419](https://github.com/future-agi/future-agi/commit/874f419831e77006624e85dd1c57c5bde5703dae))
* **tracer:** enforce the Retell deadline inside a page; never complete a bootstrap without coverage ([a61f41e](https://github.com/future-agi/future-agi/commit/a61f41e5794082f26f74882710dbf94ff32be0cb))
* **tracer:** page-level checkpointing and bounded hydration for the Retell poll ([9315485](https://github.com/future-agi/future-agi/commit/931548504cb2ff8436f55396bd54f0e1449d3900))
* **tracer:** poll Retell for voice observability instead of webhooks (cherry-pick of [#2556](https://github.com/future-agi/future-agi/issues/2556)) ([7de3803](https://github.com/future-agi/future-agi/commit/7de3803360339d54c9a93621878aed845f01c618))
* **tracer:** renumber the poll_state migration to follow 0097 on main ([0a7f04c](https://github.com/future-agi/future-agi/commit/0a7f04cf51c341551f05a7f972142c7f69e29731))
* **traces:** scope the deep-link guard to the link's own param [TH-7792] ([4f98c72](https://github.com/future-agi/future-agi/commit/4f98c72fbf12831a53a71e1c0a3be7cd5af35ce1))

## [1.35.0](https://github.com/future-agi/future-agi/compare/v1.34.1...v1.35.0) (2026-09-04)


### Features

* **error-feed:** add source identifier (Scanner/Eval) to Error Feed rows [TH-7813] ([76562ee](https://github.com/future-agi/future-agi/commit/76562ee5211b37c1ef739e3c487e946b2c223f07))


### Bug Fixes

* **catalog:** allow the production lifecycle controller past the startup guard ([054493a](https://github.com/future-agi/future-agi/commit/054493a1109df728e889429e1723cde6a68c1604))
* **catalog:** continue lifecycle from completed physical snapshots ([e1de057](https://github.com/future-agi/future-agi/commit/e1de057a6e9839acb6dc657e4b6bc9338b5d15d9))
* **catalog:** continue lifecycle from completed physical snapshots ([41c9ac3](https://github.com/future-agi/future-agi/commit/41c9ac334800da977207100779ab3af36fb24221))
* **catalog:** decode native UUID control fields ([45743a8](https://github.com/future-agi/future-agi/commit/45743a8cd4b6e14f9f3e5565e685d3ab87635709))
* **catalog:** give the consumer checkpoint inventory its own timeout ([64638d4](https://github.com/future-agi/future-agi/commit/64638d4239cee7eb894c13ad8dcded877722b5ce))
* **catalog:** restore runtime startup and completed backfill recovery ([e6edef3](https://github.com/future-agi/future-agi/commit/e6edef3d6aaa6e3bedddc76dce715540145cd2e7))
* **catalog:** validate replicated runtime and recover backfill checkpoints ([df235c1](https://github.com/future-agi/future-agi/commit/df235c19363b87cf28aa2e492d472b7fb4bb3be8))
* **error-feed:** stop mangling acronyms and duplicating eval chip labels ([552e5fb](https://github.com/future-agi/future-agi/commit/552e5fbd435cadae659130072fae01455d7462fb))
* **release:** allow deployment bump retry ([9b7423d](https://github.com/future-agi/future-agi/commit/9b7423d2d013a44150f1817ef1da8f91391baf18))

## [1.34.1](https://github.com/future-agi/future-agi/compare/v1.34.0...v1.34.1) (2026-09-03)


### Bug Fixes

* **catalog:** bind consumer to the configured production database ([91833ed](https://github.com/future-agi/future-agi/commit/91833ed2d3710fb8fa0b3b92a427d39e31e8464d))
* **catalog:** bind consumer to the configured production database ([b00ff52](https://github.com/future-agi/future-agi/commit/b00ff5220c7c9f1077428f49988f45253ef70d1d))

## [1.34.0](https://github.com/future-agi/future-agi/compare/v1.33.0...v1.34.0) (2026-09-03)


### Features

* **property-catalog:** add guarded production activation ([11b8626](https://github.com/future-agi/future-agi/commit/11b8626a0645d69db65d825a6a9b1908b8ba6a20))
* **property-catalog:** remove fixed workspace ingestion cap ([c387ca6](https://github.com/future-agi/future-agi/commit/c387ca64e211489d2cbe181b6f08d1d61c2e9bd1))
* **property-catalog:** support global production read scope ([e1ff488](https://github.com/future-agi/future-agi/commit/e1ff4884d64202621b7bcfe69328566a509e3fd1))


### Bug Fixes

* **alerts:** give the alert email CTA an absolute, region-correct URL ([63f51e9](https://github.com/future-agi/future-agi/commit/63f51e9650ce2dcaeb2cb04ca8f7c8895401c26c))
* **alerts:** give the alert email CTA an absolute, region-correct URL ([ec6a4f8](https://github.com/future-agi/future-agi/commit/ec6a4f84ca583ee9edaed94173c99e0d3a94ce2e))
* **alerts:** reopen a saved alert on its stored values, not the form defaults ([f06f3d6](https://github.com/future-agi/future-agi/commit/f06f3d670ce893e038a9b38d4c4e86cfff68ec97))
* **alerts:** reopen a saved alert on its stored values, not the form defaults ([e209d89](https://github.com/future-agi/future-agi/commit/e209d89e3c773a139255d189f7836a51b104916e))
* **alerts:** send a total notification payload so switching to email clears Slack ([7768c31](https://github.com/future-agi/future-agi/commit/7768c316f6ca51dde1664bc46b15b9d14a65eb2d))
* **catalog:** allow complete interactive property reads ([8c47f1e](https://github.com/future-agi/future-agi/commit/8c47f1ef08a632669de909219cac61e45dc5ebe6))
* **eval-task:** report the stored session id on usage log rows (TH-7761) ([a723cb2](https://github.com/future-agi/future-agi/commit/a723cb2a4c78c13bb067fd3d8fa2d534b1efdb5c))
* **eval-task:** report the stored session id on usage log rows (TH-7761) ([134f54d](https://github.com/future-agi/future-agi/commit/134f54d7662463df7a2d2eb102c8e33196581d6f))
* **evals:** surface Ground Truth not being applied on the Observe eval path (TH-7765) ([41a7af9](https://github.com/future-agi/future-agi/commit/41a7af902f0620615ce0c4698d742ac87867b42a))
* **filters:** serialize datetime filter values as true UTC instants ([#1339](https://github.com/future-agi/future-agi/issues/1339)) ([c37283f](https://github.com/future-agi/future-agi/commit/c37283f1db768038fab10939e98f2d8121f4fe67))
* **observe:** optimize filtered graphs and dashboard reads ([07ea766](https://github.com/future-agi/future-agi/commit/07ea76605ae18710d293f146024fca20d9499587))
* **observe:** optimize heavy graph and dashboard reads ([d368edc](https://github.com/future-agi/future-agi/commit/d368edc63d49379f2c533cbba64e64fc43f0cb52))
* **observe:** preserve exact empty graph contract ([ccb3904](https://github.com/future-agi/future-agi/commit/ccb3904dc36bfb471c7b3524ddc5d86432c9dca0))
* **observe:** say Error Feed, not agent compass, on the sampling rate copy [TH-7797] ([f97a6d1](https://github.com/future-agi/future-agi/commit/f97a6d1b1eb8d6293d0f2593811a6fa8a3b9338f))
* **observe:** say Error Feed, not agent compass, on the sampling rate copy [TH-7797] ([b2356f4](https://github.com/future-agi/future-agi/commit/b2356f4d9ed28a36157486923e4be6e4e051b6a7))
* **property-catalog:** honor bounded read settings ([467f611](https://github.com/future-agi/future-agi/commit/467f611852488a9d2e43cee1d0c8c69dcba402b5))
* reject duplicate saved-view names in observability ([bd23d56](https://github.com/future-agi/future-agi/commit/bd23d56ff78aad3ccdcfb56c253fe6580ccfe2b4))
* skip max cell value length check for media type uploads ([32e3ede](https://github.com/future-agi/future-agi/commit/32e3ede3afcb387eea7cec8f3fdae77168dcd3d3))
* skip max cell value length check for media type uploads ([fda8d2e](https://github.com/future-agi/future-agi/commit/fda8d2ed6fb9a921331c0cf889721eec675acbb4))
* **traces:** allow content hydration to use request wall ([23c4e03](https://github.com/future-agi/future-agi/commit/23c4e03d5f0899f080a937cd1d5331c5863d7b70))
* **tracing:** avoid full-history anchor bound scan ([b656db4](https://github.com/future-agi/future-agi/commit/b656db482d1ae73526c8ab4721c517ef3b6703ff))
* **tracing:** preserve short-window graph read budget ([91a26be](https://github.com/future-agi/future-agi/commit/91a26be213fd17291508d5c6181ab533765d145e))

## [1.33.0](https://github.com/future-agi/future-agi/compare/v1.32.0...v1.33.0) (2026-09-01)


### Features

* consolidate property catalog release and observe fixes ([2486591](https://github.com/future-agi/future-agi/commit/24865916e3e904ff9e19758253631400e6f6aca4))


### Bug Fixes

* **api:** refresh property catalog contract ([02e73fb](https://github.com/future-agi/future-agi/commit/02e73fbe92d3992d2b9d50fe0772eef07cb61b36))
* **api:** refresh property catalog contract ([de99ecb](https://github.com/future-agi/future-agi/commit/de99ecb35376a0de45694bd62dfd417fc9ae2202))
* **dashboards:** accelerate exact filtered queries ([d6c3664](https://github.com/future-agi/future-agi/commit/d6c3664fbf7c075efd22b9c6da97fde4ad88d667))
* **dashboards:** preserve catalog search loading state ([d99c6c9](https://github.com/future-agi/future-agi/commit/d99c6c98155da9a481c72dbe95f0b841ba9cc50f))
* filter + show Total Tokens column on the Sessions grid ([#2389](https://github.com/future-agi/future-agi/issues/2389)) ([0c96213](https://github.com/future-agi/future-agi/commit/0c96213e31ad6c910638c2f5ba1d8cc38fed0f8f))
* **filters:** normalize picker option values ([46d2c4f](https://github.com/future-agi/future-agi/commit/46d2c4f0df5aa851f56bd6241aafa8677dd9b6eb))
* harden property catalog observe and continuation flows ([63d410d](https://github.com/future-agi/future-agi/commit/63d410dcc7bcc6a88ef910453f5c866eac469374))
* **observe:** accelerate exact value filters ([b1ab435](https://github.com/future-agi/future-agi/commit/b1ab435fe40b2e6ac0ec5bf4ea1955119a4c3385))
* **observe:** execute exact analytics reads inline ([404e57e](https://github.com/future-agi/future-agi/commit/404e57e4edfe828864abca3e48f1f8ee403f7acb))
* **observe:** index exact unicode attribute filters ([480becd](https://github.com/future-agi/future-agi/commit/480becd0d98d38c402d3d94094405e3efd0b61ea))
* **observe:** route exact text filters through indexed anchor ([f05253d](https://github.com/future-agi/future-agi/commit/f05253dabfaf3ffec5ec1989191e280635706e39))
* **observe:** show filter loading without delay ([6bb5fcc](https://github.com/future-agi/future-agi/commit/6bb5fccf434403350181e7e21e5732d540ddf71e))
* **tracer:** bound optional witness fallback ([17b8cb1](https://github.com/future-agi/future-agi/commit/17b8cb1617af3f5a68c2bba6d8abdb186de6ecd5))
* **tracing:** honor authenticated project workspace scope ([08738a0](https://github.com/future-agi/future-agi/commit/08738a03a8fcbee8de70fa9293979a9f8916642c))

## [1.32.0](https://github.com/future-agi/future-agi/compare/v1.31.0...v1.32.0) (2026-08-31)


### Features

* **system-evals:** add seven insurance agent system evals ([ea2c238](https://github.com/future-agi/future-agi/commit/ea2c238f0ea79e194b725352a7d054d8db1f23f0))
* **system-evals:** add seven insurance agent system evals and bump seed version ([5520980](https://github.com/future-agi/future-agi/commit/5520980d0856f2a6232000d4a9ad320c8f344f82))

## [1.31.0](https://github.com/future-agi/future-agi/compare/v1.30.0...v1.31.0) (2026-08-27)


### Features

* group-only #tech nudges (drop owner DMs) ([bf7bad6](https://github.com/future-agi/future-agi/commit/bf7bad6c53cbc07328b13b3f83ca549c5d5cf2ac))
* group-only #tech nudges for shipped-but-open tickets ([968f0d6](https://github.com/future-agi/future-agi/commit/968f0d642cb2d19eb0a1d1aa2841e67a6b779dc4))
* nudge owners for in-flight tickets instead of force-closing ([aa99426](https://github.com/future-agi/future-agi/commit/aa9942650ab3c5dce0c2713c2259be550d3bb13b))
* reconcile release tickets by state + fix discovery ([f605cd5](https://github.com/future-agi/future-agi/commit/f605cd59b4a63b7ecc4694169c3d5a62d22207f7))


### Bug Fixes

* broaden release ticket discovery to all PRs in the tag range ([7b006c7](https://github.com/future-agi/future-agi/commit/7b006c73f8c2c7886423afde8645bc6c4580769c))
* **evals:** treat a non-string eval mapping value as invalid instead of crashing ([02ff5a9](https://github.com/future-agi/future-agi/commit/02ff5a9dd05761e3bed977c399ba4159ed239a16))
* **evals:** treat a non-string eval mapping value as invalid instead of crashing ([1fdd5ed](https://github.com/future-agi/future-agi/commit/1fdd5ed8f7a3a1cf7540e2097fbc47f65c43ff25))
* match only real merge formats when harvesting PR numbers ([a3d28ba](https://github.com/future-agi/future-agi/commit/a3d28bafcce0fe54a28a34bb5c9e27f1257bbf90))

## [1.30.0](https://github.com/future-agi/future-agi/compare/v1.29.1...v1.30.0) (2026-08-25)


### Features

* automating internal ticketing ([5e02f38](https://github.com/future-agi/future-agi/commit/5e02f38b9841ac3e079447e0341f91ba84621171))
* **gateway:** export caller metadata, body fields and headers as span attributes ([d9f36f2](https://github.com/future-agi/future-agi/commit/d9f36f256d7ba7ab89dfd059446fc43551d8fb88))
* **gateway:** support Anthropic server tools on the OpenAI-format endpoint ([3b42204](https://github.com/future-agi/future-agi/commit/3b42204c0abea18b5bb3f36b2a6ef3f287ff3745))


### Bug Fixes

* **gateway:** resolve provider endpoint paths through one builder ([8d4cba1](https://github.com/future-agi/future-agi/commit/8d4cba1894e6f313b15954cfdd784bf57f230ae1))
* key reasoning toggle by source_id, not sourceId ([#2321](https://github.com/future-agi/future-agi/issues/2321)) ([6ff2732](https://github.com/future-agi/future-agi/commit/6ff27322b49be7574a99aea4bfd443f6317d7c46))
* mock get_user_organization in post-registration tests ([937f4fb](https://github.com/future-agi/future-agi/commit/937f4fba643cea54d5057a79303f6ade73c79043))
* restore demo data seeding on new user signup ([f2d1e5a](https://github.com/future-agi/future-agi/commit/f2d1e5a208918ad50652e15044e06bb9e7ffae2e))
* restore demo data seeding on new user signup ([941ffff](https://github.com/future-agi/future-agi/commit/941ffff150c635d67953b424026ec3f60a1f445c))

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
