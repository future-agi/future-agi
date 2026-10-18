package server

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	rbacplugin "github.com/futureagi/agentcc-gateway/internal/plugins/rbac"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	rbacpkg "github.com/futureagi/agentcc-gateway/internal/rbac"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/lifecycle"
)

const videoTestModel = "byteplus/dreamina-seedance-2-5-260628"
const videoTestBody = `{"model":"byteplus/dreamina-seedance-2-5-260628","prompt":"private prompt"}`

type videoHTTPFixture struct {
	s                                                *Server
	client                                           *redisstate.Client
	prefix                                           string
	store                                            video.Store
	service                                          *lifecycle.Service
	blobs                                            artifacts.Store
	keyA, keyB, missingOrg, managed, denied, revoked string
}

func newVideoHTTP(t *testing.T, modify ...func(*config.VideoConfig)) *videoHTTPFixture {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	c, e := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	prefix := fmt.Sprintf("test:video:http:%d:", time.Now().UnixNano())
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Admin.Token = "test-admin-token"
	ks := auth.NewKeyStore(cfg.Auth)
	_, a := ks.Create("a", "owner", nil, nil, map[string]string{"org_id": "a"})
	_, b := ks.Create("b", "owner", nil, nil, map[string]string{"org_id": "b"})
	_, missing := ks.Create("missing", "owner", nil, nil, nil)
	m, mk := ks.Create("managed", "owner", nil, nil, map[string]string{"org_id": "a"})
	m.KeyType = "managed"
	_, deny := ks.Create("denied", "owner", []string{"different-model"}, nil, map[string]string{"org_id": "a"})
	rev, rk := ks.Create("revoked", "owner", nil, nil, map[string]string{"org_id": "a"})
	ks.Revoke(rev.ID)
	engine := pipeline.NewEngine(authplugin.New(ks, true))
	registry, e := providers.NewRegistry(cfg)
	if e != nil {
		t.Fatal(e)
	}
	s := New(cfg, "", registry, engine, ks, nil, nil, metrics.NewRegistry(), testModelDBPtr(), tenant.NewStore(), nil)
	vc := cfg.Video
	vc.Enabled = true
	vc.Submit.SyncWait = 0
	vc.Artifacts.Disk.Root = t.TempDir()
	vc.Providers["byteplus"] = config.VideoProviderConfig{Enabled: true, Models: []string{strings.TrimPrefix(videoTestModel, "byteplus/")}, Region: "ap-southeast-1", AccountRef: "fake", TariffRevision: capability.BytePlusRevision, Limits: config.VideoProviderLimits{SubmitRPM: 100, PollQPS: 100, MaxActiveTasks: 100}, Deadlines: config.VideoProviderDeadlines{Submit: time.Second, Read: time.Second, Run: time.Hour, Reconcile: time.Second}, Poll: config.VideoProviderPoll{BaseInterval: time.Second}}
	f := videotest.NewFakeProvider(videotest.Script{Capabilities: capability.BytePlus(), Submit: videotest.JSONReply(provider.SubmitResult{ProviderJobID: "fake-id", Normalized: provider.StateQueued})})
	adapters := provider.NewRegistry()
	adapters.Register(f.Adapter())
	budget := redisstate.NewBudgetStore(c, prefix+"budget:")
	st := video.NewRedisStore(c, video.StoreOptions{Prefix: prefix, Budget: budget})
	blobs, e := artifacts.NewDisk(vc.Artifacts.Disk.Root, vc.Artifacts.MaxBytes)
	if e != nil {
		t.Fatal(e)
	}
	for _, fn := range modify {
		fn(&vc)
	}
	svc, e := lifecycle.New(lifecycle.Options{Config: vc, Store: st, Redis: c, Budget: budget, Adapters: adapters, Artifacts: blobs, Engine: engine, Prefix: prefix})
	if e != nil {
		t.Fatal(e)
	}
	s.handlers.videoStore = st
	s.handlers.videoService = svc
	s.handlers.videoSyncWait = 0
	t.Cleanup(func() {
		s.Shutdown(context.Background())
		f.Close()
		keys, _ := c.Redis().Keys(context.Background(), prefix+"*").Result()
		if len(keys) > 0 {
			c.Redis().Del(context.Background(), keys...)
		}
		c.Close()
	})
	return &videoHTTPFixture{s: s, client: c, prefix: prefix, store: st, service: svc, blobs: blobs, keyA: a, keyB: b, missingOrg: missing, managed: mk, denied: deny, revoked: rk}
}
func (f *videoHTTPFixture) request(method, path, key, idem, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if key != "" {
		r.Header.Set("Authorization", "Bearer "+key)
	}
	if idem != "" {
		r.Header.Set("Idempotency-Key", idem)
	}
	w := httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	return w
}
func jsonObject(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var o map[string]any
	if e := json.Unmarshal(w.Body.Bytes(), &o); e != nil {
		t.Fatal(w.Code, w.Body.String())
	}
	return o
}
func (f *videoHTTPFixture) submit(t *testing.T) string {
	t.Helper()
	w := f.request("POST", "/v1/videos", f.keyA, video.NewID(), videoTestBody)
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	return jsonObject(t, w)["id"].(string)
}
func TestVideoIdempotencyAndPipeline(t *testing.T) {
	f := newVideoHTTP(t)
	w := f.request("POST", "/v1/videos", f.keyA, "same", videoTestBody)
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	id := jsonObject(t, w)["id"]
	replay := f.request("POST", "/v1/videos", f.keyA, "same", videoTestBody)
	if replay.Code != 202 || jsonObject(t, replay)["id"] != id {
		t.Fatal(replay.Body.String())
	}
	changed := f.request("POST", "/v1/videos", f.keyA, "same", strings.Replace(videoTestBody, "private prompt", "different", 1))
	if changed.Code != 409 {
		t.Fatal(changed.Code, changed.Body.String())
	}
	other := f.request("POST", "/v1/videos", f.keyB, "same", videoTestBody)
	if other.Code != 202 || jsonObject(t, other)["id"] == id {
		t.Fatal(other.Code, other.Body.String())
	}
}
func TestVideoAuthAndValidation(t *testing.T) {
	f := newVideoHTTP(t)
	for _, tc := range []struct {
		name, key, idem, body, code string
		status                      int
	}{{"missing key", f.keyA, "", videoTestBody, "missing_idempotency_key", 400}, {"no org", f.missingOrg, "new", videoTestBody, "missing_org", 403}, {"revoked", f.revoked, "new", videoTestBody, "", 401}, {"model denied", f.denied, "new", videoTestBody, "model_forbidden", 403}, {"managed", f.managed, "new", videoTestBody, "managed_tariff_not_configured", 503}, {"unknown", f.keyA, "new", `{"model":"unknown","prompt":"x"}`, "unsupported_model", 400}, {"field", f.keyA, "new", `{"model":"unknown","prompt":"x","org_id":"foreign"}`, "unknown_field", 400}, {"options", f.keyA, "new", strings.Replace(videoTestBody, "}", `,"provider_options":{"bad":true}}`, 1), "unknown_provider_option", 400}} {
		t.Run(tc.name, func(t *testing.T) {
			w := f.request("POST", "/v1/videos", tc.key, tc.idem, tc.body)
			if w.Code != tc.status || (tc.code != "" && !strings.Contains(w.Body.String(), tc.code)) {
				t.Fatal(w.Code, w.Body.String())
			}
		})
	}
	jobs, e := f.store.ListAccounting(context.Background())
	if e != nil || len(jobs) != 0 {
		t.Fatal("rejected request accepted", len(jobs), e)
	}
}
func TestVideoTenantIsolationAllRoutes(t *testing.T) {
	f := newVideoHTTP(t)
	id := f.submit(t)
	for _, tc := range []struct{ method, suffix string }{{"GET", ""}, {"GET", "/content"}, {"POST", "/cancel"}, {"DELETE", ""}} {
		foreign := f.request(tc.method, "/v1/videos/"+id+tc.suffix, f.keyB, "", "")
		missing := f.request(tc.method, "/v1/videos/video_missing"+tc.suffix, f.keyB, "", "")
		if foreign.Code != 404 || foreign.Body.String() != missing.Body.String() {
			t.Fatalf("%s %s: %d %s != %d %s", tc.method, tc.suffix, foreign.Code, foreign.Body.String(), missing.Code, missing.Body.String())
		}
	}
	list := f.request("GET", "/v1/videos", f.keyB, "", "")
	if list.Code != 200 || jsonObject(t, list)["total"] != float64(0) {
		t.Fatal(list.Code, list.Body.String())
	}
	f.client.Redis().HSet(context.Background(), f.prefix+"job:"+id, "org_id", "")
	ownerless := f.request("GET", "/v1/videos/"+id, f.keyA, "", "")
	if ownerless.Code != 404 {
		t.Fatal(ownerless.Code)
	}
}
func TestVideoDeleteLocalOnlyAndReplay(t *testing.T) {
	f := newVideoHTTP(t)
	w := f.request("POST", "/v1/videos", f.keyA, "delete-key", videoTestBody)
	id := jsonObject(t, w)["id"].(string)
	for i := 0; i < 2; i++ {
		del := f.request("DELETE", "/v1/videos/"+id, f.keyA, "", "")
		if del.Code != 200 || jsonObject(t, del)["deletion_scope"] != "local_only" || jsonObject(t, del)["accounting_retained"] != true {
			t.Fatal(del.Code, del.Body.String())
		}
	}
	replay := f.request("POST", "/v1/videos", f.keyA, "delete-key", videoTestBody)
	if replay.Code != 410 || !strings.Contains(replay.Body.String(), "deleted_job") {
		t.Fatal(replay.Code, replay.Body.String())
	}
}
func TestVideoContentRangeAndPrivacy(t *testing.T) {
	f := newVideoHTTP(t)
	id := f.submit(t)
	j, e := f.store.GetForOrg(id, "a")
	if e != nil {
		t.Fatal(e)
	}
	l, e := f.store.Lease(context.Background(), id, "test", time.Minute)
	if e != nil {
		t.Fatal(e)
	}
	j.Phase = video.PhaseCalling
	f.store.Save(context.Background(), j, l)
	media := videotest.MP4()
	key := "video/" + id + "/output-0.mp4"
	_, e = f.blobs.Put(context.Background(), key, bytes.NewReader(media), artifacts.Meta{ContentType: "video/mp4", ExpiresAt: time.Now().Add(time.Hour)})
	if e != nil {
		t.Fatal(e)
	}
	j.Status = video.StatusCompleted
	j.ProviderJobID = "supplier-id"
	j.Artifacts = []video.Artifact{{Index: 0, State: video.ArtifactAvailable, ContentType: "video/mp4", Bytes: int64(len(media)), BlobKey: key, ExpiresAt: time.Now().Add(time.Hour)}}
	if e = f.store.Save(context.Background(), j, l); e != nil {
		t.Fatal(e)
	}
	f.store.Release(context.Background(), l)
	r := httptest.NewRequest("GET", "/v1/videos/"+id+"/content", nil)
	r.Header.Set("Authorization", "Bearer "+f.keyA)
	r.Header.Set("Range", "bytes=0-9")
	w := httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 206 || !bytes.Equal(w.Body.Bytes(), media[:10]) || w.Header().Get("Content-Range") == "" || w.Header().Get("Accept-Ranges") != "bytes" {
		t.Fatal(w.Code, w.Header(), w.Body.String())
	}
	status := f.request("GET", "/v1/videos/"+id, f.keyA, "", "")
	if strings.Contains(status.Body.String(), "private prompt") || strings.Contains(status.Body.String(), key) {
		t.Fatal("private fields exposed", status.Body.String())
	}
	r.Header.Set("Range", "bytes=999999-")
	w = httptest.NewRecorder()
	f.s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 416 {
		t.Fatal(w.Code)
	}
}
func TestVideoListInvalidFilters(t *testing.T) {
	f := newVideoHTTP(t)
	for _, q := range []string{"limit=0", "limit=101", "offset=-1", "offset=x", "order=bad", "status=bad", "model=bad"} {
		w := f.request("GET", "/v1/videos?"+q, f.keyA, "", "")
		if w.Code != 400 || !strings.Contains(w.Body.String(), "invalid_filter") {
			t.Fatal(q, w.Code, w.Body.String())
		}
	}
}
func TestVideoDisabled501(t *testing.T) {
	f := newVideoHTTP(t)
	f.s.handlers.videoStore = nil
	for _, tc := range []struct{ method, path string }{{"POST", "/v1/videos"}, {"GET", "/v1/videos"}, {"GET", "/v1/videos/id"}, {"GET", "/v1/videos/id/content"}, {"POST", "/v1/videos/id/cancel"}, {"DELETE", "/v1/videos/id"}} {
		w := f.request(tc.method, tc.path, f.keyA, "", "")
		if w.Code != 501 || !strings.Contains(w.Body.String(), "not_configured") {
			t.Fatal(tc, w.Code, w.Body.String())
		}
	}
}

func TestVideoKillSwitchStoreOutageAndStatusOnly(t *testing.T) {
	f := newVideoHTTP(t)
	id := f.submit(t)
	if e := f.service.SetKillSwitch(context.Background(), true); e != nil {
		t.Fatal(e)
	}
	w := f.request("POST", "/v1/videos", f.keyA, "killed", videoTestBody)
	if w.Code != 503 || !strings.Contains(w.Body.String(), "video_submissions_disabled") {
		t.Fatal(w.Code, w.Body.String())
	}
	for i := 0; i < 3; i++ {
		w = f.request("GET", "/v1/videos/"+id, f.keyA, "", "")
		if w.Code != 200 || jsonObject(t, w)["status"] != "submitting" {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	// Corrupt only this test's key type to exercise fail-closed store behavior.
	f.client.Redis().Del(context.Background(), f.prefix+"job:"+id)
	f.client.Redis().Set(context.Background(), f.prefix+"job:"+id, "unavailable", 0)
	w = f.request("GET", "/v1/videos/"+id, f.keyA, "", "")
	if w.Code != 503 || !strings.Contains(w.Body.String(), "video_store_unavailable") {
		t.Fatal(w.Code, w.Body.String())
	}
}
func TestVideoAllowedProvidersAndRBAC(t *testing.T) {
	f := newVideoHTTP(t)
	_, key := f.s.keyStore.Create("provider-denied", "owner", nil, []string{"openai"}, map[string]string{"org_id": "a"})
	w := f.request("POST", "/v1/videos", key, "denied", videoTestBody)
	if w.Code != 403 || !strings.Contains(w.Body.String(), "provider_not_allowed") {
		t.Fatal(w.Code, w.Body.String())
	}
	// The actual RBAC plugin must run even though no chat provider is resolved.
	store := rbacpkg.NewStore(config.RBACConfig{Enabled: true, DefaultRole: "restricted", Roles: map[string]config.RBACRoleConfig{"restricted": {Permissions: []string{"models:other"}}}})
	f.s.handlers.engine = pipeline.NewEngine(authplugin.New(f.s.keyStore, true), rbacplugin.New(store, true))
	w = f.request("POST", "/v1/videos", f.keyA, "rbac", videoTestBody)
	if w.Code != 403 || !strings.Contains(w.Body.String(), "model_forbidden") {
		t.Fatal(w.Code, w.Body.String())
	}
}
func TestVideoListFiltersOrderAndCount(t *testing.T) {
	f := newVideoHTTP(t)
	var ids []string
	for i := 0; i < 3; i++ {
		ids = append(ids, f.submit(t))
	}
	w := f.request("GET", "/v1/videos?limit=2&order=desc&status=submitting&model="+videoTestModel, f.keyA, "", "")
	o := jsonObject(t, w)
	if w.Code != 200 || o["total"] != float64(3) || o["has_more"] != true || len(o["data"].([]any)) != 2 {
		t.Fatal(w.Code, w.Body.String())
	}
	rows := o["data"].([]any)
	if rows[0].(map[string]any)["id"] != ids[2] || rows[1].(map[string]any)["id"] != ids[1] {
		t.Fatal(rows)
	}
	f.request("DELETE", "/v1/videos/"+ids[1], f.keyA, "", "")
	w = f.request("GET", "/v1/videos?offset=1", f.keyA, "", "")
	o = jsonObject(t, w)
	if o["total"] != float64(2) || len(o["data"].([]any)) != 1 {
		t.Fatal(w.Body.String())
	}
	w = f.request("GET", "/v1/videos?status=completed", f.keyA, "", "")
	if jsonObject(t, w)["total"] != float64(0) {
		t.Fatal(w.Body.String())
	}
}
func TestVideoStartDisabledAndFailedInitialization(t *testing.T) {
	f := newVideoHTTP(t)
	cfg := config.DefaultConfig()
	cfg.Video.Enabled = true
	cfg.Video.Artifacts.Disk.Root = "/dev/null/cannot-create"
	reg, e := providers.NewRegistry(cfg)
	if e != nil {
		t.Fatal(e)
	}
	s := New(cfg, "", reg, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	defer s.Shutdown(context.Background())
	if s.Start() == nil {
		t.Fatal("start ignored artifact initialization failure")
	}
	cfg = config.DefaultConfig()
	reg, e = providers.NewRegistry(cfg)
	if e != nil {
		t.Fatal(e)
	}
	s2 := New(cfg, "", reg, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	defer s2.Shutdown(context.Background())
	if s2.handlers.videoStore != nil || s2.videoWorker != nil {
		t.Fatal("disabled video initialized")
	}
	_ = f
}

func TestVideoSubmitConfigurationErrors(t *testing.T) {
	for _, tc := range []struct {
		name, code string
		status     int
		change     func(*config.VideoConfig)
	}{
		{"disabled adapter", "provider_not_configured", 503, func(c *config.VideoConfig) {
			p := c.Providers["byteplus"]
			p.Enabled = false
			c.Providers["byteplus"] = p
		}},
		{"tariff", "tariff_missing", 503, func(c *config.VideoConfig) {
			p := c.Providers["byteplus"]
			p.TariffRevision = "missing"
			c.Providers["byteplus"] = p
		}},
		{"region", "unsupported_model", 400, func(c *config.VideoConfig) {
			p := c.Providers["byteplus"]
			p.Region = "wrong-region"
			c.Providers["byteplus"] = p
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newVideoHTTP(t, tc.change)
			w := f.request("POST", "/v1/videos", f.keyA, "key", videoTestBody)
			if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.code) {
				t.Fatal(w.Code, w.Body.String())
			}
		})
	}
}
func TestVideoOrgRateLimit(t *testing.T) {
	f := newVideoHTTP(t, func(c *config.VideoConfig) { c.Limits.OrgSubmitRPM = 1 })
	f.submit(t)
	w := f.request("POST", "/v1/videos", f.keyA, "second", videoTestBody)
	if w.Code != 429 || !strings.Contains(w.Body.String(), "rate_limit_exceeded") || w.Header().Get("Retry-After") == "" {
		t.Fatal(w.Code, w.Body.String(), w.Header())
	}
}

func TestVideoIngressVerifiedAndWorkerUsesStoredMedia(t *testing.T) {
	f := newVideoHTTP(t)
	var imageBytes bytes.Buffer
	if e := png.Encode(&imageBytes, image.NewRGBA(image.Rect(0, 0, 300, 300))); e != nil {
		t.Fatal(e)
	}
	req := provider.Request{Model: videoTestModel, Prompt: "private prompt", EndUserID: "private-end-user", Inputs: []provider.Input{{Role: provider.FirstFrame, MediaType: "image/png", Source: provider.Source{Data: base64.StdEncoding.EncodeToString(imageBytes.Bytes())}}}}
	body, _ := json.Marshal(req)
	w := f.request("POST", "/v1/videos", f.keyA, "media", string(body))
	if w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	id := jsonObject(t, w)["id"].(string)
	j, e := f.store.GetAccounting(context.Background(), id)
	if e != nil {
		t.Fatal(e)
	}
	if len(j.Ingress) != 1 || j.Ingress[0].Digest == "" || strings.Contains(string(j.RequestCanonical), "private-end-user") || strings.Contains(string(j.RequestCanonical), req.Inputs[0].Source.Data) {
		t.Fatal("unverified or raw media stored")
	}
	if e = f.service.NewWorker().Tick(context.Background()); e != nil {
		t.Fatal(e)
	}
	j, e = f.store.GetAccounting(context.Background(), id)
	if e != nil || j.Status != video.StatusQueued {
		t.Fatal(j.Status, e)
	}
	req.Inputs[0].Source.Data = base64.StdEncoding.EncodeToString([]byte("invalid"))
	body, _ = json.Marshal(req)
	w = f.request("POST", "/v1/videos", f.keyA, "bad-media", string(body))
	if w.Code != 400 {
		t.Fatal(w.Code, w.Body.String())
	}
}
func TestVideoStartupWorkerAndShutdown(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Video.Enabled = true
	cfg.Video.Store = "memory"
	cfg.Video.AllowNonDurableStore = true
	cfg.Video.Artifacts.Disk.Root = t.TempDir()
	reg, e := providers.NewRegistry(cfg)
	if e != nil {
		t.Fatal(e)
	}
	s := New(cfg, "", reg, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	if s.videoInitErr != nil || s.videoWorker == nil || s.handlers.videoService == nil {
		t.Fatal(s.videoInitErr)
	}
	s.videoWorker.Start()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if e = s.Shutdown(ctx); e != nil {
		t.Fatal(e)
	}
}

func TestVideoMetricsEndpointRegistered(t *testing.T) {
	f := newVideoHTTP(t)
	w := f.request("GET", "/-/metrics", "test-admin-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "# TYPE video_jobs_total counter") || !strings.Contains(w.Body.String(), "# TYPE video_unsettled_micros gauge") {
		t.Fatal(w.Code, w.Body.String())
	}
}
