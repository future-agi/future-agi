package server

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"mime/multipart"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	budgettracker "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

func TestReviewEmptySecondsRefusedBeforeAcceptance(t *testing.T) {
	f := newVideoHTTP(t)
	for _, seconds := range []string{"null", `""`, `" "`, "0"} {
		w := f.request("POST", "/v1/videos", f.keyA, video.NewID(), fmt.Sprintf(`{"model":%q,"prompt":"test","seconds":%s}`, videoTestModel, seconds))
		if w.Code != 400 || !strings.Contains(w.Body.String(), `"type":"invalid_request_error"`) {
			t.Errorf("seconds=%s got %d %s", seconds, w.Code, w.Body.String())
		}
	}
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	form.WriteField("model", videoTestModel)
	form.WriteField("prompt", "test")
	form.WriteField("seconds", "")
	form.Close()
	w := f.form(f.keyA, video.NewID(), body.String(), form.FormDataContentType())
	if w.Code != 400 {
		t.Errorf("empty multipart seconds got %d", w.Code)
	}
	jobs, err := f.store.ListAccounting(context.Background())
	if err != nil || len(jobs) != 0 {
		t.Fatalf("invalid seconds accepted %d jobs: %v", len(jobs), err)
	}
	keys, err := f.client.Redis().Keys(context.Background(), f.prefix+"reservation:*").Result()
	if err != nil || len(keys) != 0 {
		t.Fatalf("invalid seconds reserved: %v %v", keys, err)
	}
	// An omitted seconds field in the OpenAI dialect must keep its default.
	w = f.request("POST", "/v1/videos", f.keyA, video.NewID(), fmt.Sprintf(`{"model":%q,"prompt":"test","size":"854x480"}`, videoTestModel))
	if w.Code != 202 {
		t.Fatalf("omitted default refused %d %s", w.Code, w.Body.String())
	}
}

func completedVideoFixture(t *testing.T, f *videoHTTPFixture) string {
	t.Helper()
	id := f.submit(t)
	ctx := context.Background()
	j, err := f.store.GetAccounting(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	j.Phase = video.PhaseCalling
	if err = f.store.Update(j); err != nil {
		t.Fatal(err)
	}
	key := "video/" + id + "/output-0.mp4"
	body := videotest.MP4()
	if _, err = f.blobs.Put(ctx, key, bytes.NewReader(body), artifacts.Meta{ContentType: "video/mp4"}); err != nil {
		t.Fatal(err)
	}
	j.Status = video.StatusCompleted
	j.Phase = video.PhaseReceived
	j.ProviderJobID = "completed"
	j.Artifacts = []video.Artifact{{BlobKey: key, State: video.ArtifactAvailable, ExpiresAt: time.Now().Add(time.Hour), ContentType: "video/mp4", Bytes: int64(len(body))}}
	if err = f.store.Update(j); err != nil {
		t.Fatal(err)
	}
	return id
}
func TestReviewVideoRoutesEnforceNetworkAccess(t *testing.T) {
	for _, scope := range []string{"global", "org"} {
		for _, route := range []string{"create", "list", "get", "content", "delete", "cancel"} {
			t.Run(scope+"/"+route, func(t *testing.T) {
				f := newVideoHTTP(t)
				id := completedVideoFixture(t, f)
				cfg := f.s.cfg
				if scope == "global" {
					cfg.IPACL = config.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}
				} else {
					f.s.handlers.tenantStore.Set("a", &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"10.0.0.0/8"}}})
				}
				// Use the real Server constructor to bind its network configuration.
				registry, err := providers.NewRegistry(cfg)
				if err != nil {
					t.Fatal(err)
				}
				s := New(cfg, "", registry, f.s.handlers.engine, f.s.handlers.keyStore, nil, nil, metrics.NewRegistry(), testModelDBPtr(), f.s.handlers.tenantStore, nil)
				s.handlers.videoStore = f.store
				s.handlers.videoService = f.service
				s.handlers.videoSyncWait = 0
				t.Cleanup(func() { s.Shutdown(context.Background()) })
				method, path, body := "GET", "/v1/videos/"+id, ""
				switch route {
				case "create":
					method = "POST"
					path = "/v1/videos"
					body = videoTestBody
				case "list":
					path = "/v1/videos"
				case "content":
					path += "/content"
				case "delete":
					method = "DELETE"
				case "cancel":
					method = "POST"
					path += "/cancel"
				}
				request := func(ip string) *httptest.ResponseRecorder {
					r := httptest.NewRequest(method, path, strings.NewReader(body))
					r.RemoteAddr = ip + ":12345"
					r.Header.Set("Authorization", "Bearer "+f.keyA)
					r.Header.Set("Idempotency-Key", video.NewID())
					r.Header.Set("Content-Type", "application/json")
					w := httptest.NewRecorder()
					s.httpServer.Handler.ServeHTTP(w, r)
					return w
				}
				if w := request("192.0.2.1"); w.Code != 403 {
					t.Fatalf("denied IP got %d %s", w.Code, w.Body.String())
				}
				if _, err = f.store.GetForOrg(id, "a"); err != nil {
					t.Fatal("denied mutation changed owner job", err)
				}
				if w := request("10.0.0.2"); w.Code != 200 && !(route == "create" && w.Code == 202) {
					t.Fatalf("allowed IP got %d %s", w.Code, w.Body.String())
				}
			})
		}
	}
}

func TestReviewConfiguredVideoNamespacesAreIsolated(t *testing.T) {
	a, b := newVideoHTTP(t), newVideoHTTP(t)
	for i, f := range []*videoHTTPFixture{a, b} {
		f.s.cfg.Redis.Prefix = a.prefix + fmt.Sprintf("installation-%d:", i)
		f.s.cfg.Video.Store = "redis"
		f.s.cfg.Video.Providers = map[string]config.VideoProviderConfig{}
		f.s.cfg.Video.Artifacts.Backend = "disk"
		f.s.cfg.Video.Artifacts.Disk.Root = t.TempDir()
		if err := f.s.configureVideo(f.client); err != nil {
			t.Fatal(err)
		}
	}
	ctx := context.Background()
	j := &video.VideoJob{ID: video.NewID(), OrgID: "same-org", Status: video.StatusSubmitting, Phase: video.PhasePrepared, CreatedAt: time.Now(), ExpiresAt: time.Now().Add(time.Hour), Fingerprint: "same-request"}
	first, err := a.s.handlers.videoStore.Accept(ctx, video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "same-key"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = b.s.handlers.videoStore.GetForOrg(first.Job.ID, j.OrgID); !errors.Is(err, video.ErrVideoJobNotFound) {
		t.Errorf("second installation sees first job: %v", err)
	}
	claims, err := b.s.handlers.videoStore.ClaimSubmit(ctx, "other-worker", time.Minute, 10)
	if err != nil || len(claims) != 0 {
		t.Errorf("cross-installation worker claim: %d %v", len(claims), err)
	}
	j.ID = video.NewID()
	second, err := b.s.handlers.videoStore.Accept(ctx, video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "same-key"})
	if err != nil || second.Replay || second.Job.ID == first.Job.ID {
		t.Errorf("cross-installation idempotency: %+v %v", second, err)
	}
	if err = a.s.handlers.videoService.SetKillSwitch(ctx, true); err != nil {
		t.Fatal(err)
	}
	killed, err := b.s.handlers.videoService.Killed(ctx)
	if err != nil || killed {
		t.Errorf("cross-installation kill switch: %v %v", killed, err)
	}
}

func TestReviewGlobalBudgetRejectsNextHTTPVideo(t *testing.T) {
	obs := provider.Observation{Normalized: provider.StateCompleted, ProviderState: "completed", Outputs: []provider.OutputRef{{ContentType: "video/mp4"}}, Usage: &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.VideoTokens, Quantity: 300000, Source: provider.UsageProviderReported}}}}
	f := newVideoHTTPScript(t, videotest.Script{Capabilities: capability.BytePlus(), Submit: videotest.JSONReply(provider.SubmitResult{ProviderJobID: "budget-video", Normalized: provider.StateQueued}), Poll: []videotest.Reply{videotest.JSONReply(obs)}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	tracker := budgettracker.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &config.BudgetLevelConfig{Limit: 2}})
	bp := budgetplugin.New(tracker, nil, true, f.s.handlers.tenantStore)
	bp.SetRedisBudget(redisstate.NewBudgetStore(f.client, f.prefix+"budget:"))
	bp.ShareGlobalBudgets() // video gateways share global levels with the ledger
	// The handler and service share this engine, exactly as production does.
	*f.s.handlers.engine = *pipeline.NewEngine(authplugin.New(f.s.handlers.keyStore, true), costplugin.New(true, nil, nil, nil), bp)
	id := f.submit(t)
	worker := f.service.NewWorker()
	ctx := context.Background()
	if err := worker.Tick(ctx); err != nil {
		t.Fatal(err)
	}
	j, err := f.store.GetAccounting(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	j.NextPollAt = time.Now().Add(-time.Second)
	if err = f.store.Update(j); err != nil {
		t.Fatal(err)
	}
	if err = worker.Tick(ctx); err != nil {
		t.Fatal(err)
	}
	j, err = f.store.GetAccounting(ctx, id)
	if err != nil {
		t.Fatal(err)
	}
	if j.Status != video.StatusCompleted || j.Cost < 2 || j.SettlementState != video.SettlementSettled {
		t.Fatalf("job=%s cost=%v settlement=%s", j.Status, j.Cost, j.SettlementState)
	}
	if w := f.request("POST", "/v1/videos", f.keyA, video.NewID(), videoTestBody); w.Code != 429 || !strings.Contains(w.Body.String(), "budget_exceeded") {
		t.Fatalf("exhausted global budget allowed next video: %d %s", w.Code, w.Body.String())
	}
	jobs, err := f.store.ListAccounting(ctx)
	if err != nil || len(jobs) != 1 {
		t.Fatalf("extra accepted job: %d %v", len(jobs), err)
	}
}
