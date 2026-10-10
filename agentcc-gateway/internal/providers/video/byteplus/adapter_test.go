package byteplus

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

const model = "dreamina-seedance-2-5-260628"

func job() *video.JobView {
	return &video.JobView{ID: "video_test", OrgID: "org", KeyID: "key", ModelID: model, Service: "byteplus", Region: "ap-southeast-1", Request: video.Request{Model: "byteplus/" + model, Prompt: "Synthetic test", EndUserID: "user"}, AttemptStartedAt: time.Unix(1700000000, 0), SubmitBy: time.Unix(1700000030, 0)}
}
func testAdapter(t *testing.T, handler http.HandlerFunc) *Adapter {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	a, err := New(Config{Provider: config.VideoProviderConfig{APIKey: "test-api-key", BaseURL: server.URL + "/api/v3", ExecutionExpiresAfter: 7200}, CorrelationSecret: "test-correlation-secret"})
	if err != nil {
		t.Fatal(err)
	}
	a.api.Transport = server.Client().Transport
	a.download.Transport = server.Client().Transport
	return a
}
func prepare(t *testing.T, a *Adapter, j *video.JobView) video.Correlation {
	t.Helper()
	c, e := a.Prepare(context.Background(), j)
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func TestPrepare_HMAC(t *testing.T) {
	a := testAdapter(t, func(http.ResponseWriter, *http.Request) { t.Fatal("Prepare made network call") })
	j := job()
	c := prepare(t, a, j)
	h := hmac.New(sha256.New, []byte("test-correlation-secret"))
	h.Write([]byte(j.OrgID + j.KeyID + j.Request.EndUserID))
	if c.Token != hex.EncodeToString(h.Sum(nil)) || c.Data["safety_identifier"] != c.Token || strings.Contains(c.Token, "test-api-key") {
		t.Fatal(c)
	}
	j.OrgID = "other"
	if c.Token == prepare(t, a, j).Token {
		t.Fatal("cross-org correlation collision")
	}
}
func TestSubmit_RolesAndOptions(t *testing.T) {
	for _, tc := range []struct {
		name  string
		roles []video.Role
		ratio string
	}{{"first", []video.Role{video.FirstFrame}, "adaptive"}, {"first_last", []video.Role{video.FirstFrame, video.LastFrame}, "adaptive"}, {"references", []video.Role{video.ReferenceImage, video.ReferenceVideo, video.ReferenceAudio}, "16:9"}} {
		t.Run(tc.name, func(t *testing.T) {
			var received map[string]any
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "POST" || r.URL.Path != "/api/v3/contents/generations/tasks" || r.Header.Get("Authorization") != "Bearer test-api-key" {
					t.Error(r.Method, r.URL.Path)
				}
				if e := json.NewDecoder(r.Body).Decode(&received); e != nil {
					t.Error(e)
				}
				io.WriteString(w, `{"id":"cgt-test"}`)
			})
			j := job()
			j.Request.AspectRatio = "16:9"
			j.Request.ProviderOptions = map[string]any{"frames": 121, "watermark": false, "camera_fixed": true, "return_last_frame": true}
			for _, role := range tc.roles {
				typ := "image/png"
				if role == video.ReferenceVideo {
					typ = "video/mp4"
				}
				if role == video.ReferenceAudio {
					typ = "audio/mpeg"
				}
				j.Request.Inputs = append(j.Request.Inputs, video.Input{Role: role, MediaType: typ, Source: video.Source{URL: "https://media.example/synthetic"}})
			}
			corr := prepare(t, a, j)
			got, e := a.Submit(context.Background(), j, corr)
			if e != nil || got.ProviderJobID != "cgt-test" || got.Normalized != video.StateQueued {
				t.Fatal(got, e)
			}
			if received["ratio"] != tc.ratio || received["frames"] != float64(121) || received["execution_expires_after"] != float64(7200) || received["safety_identifier"] != corr.Token {
				t.Fatal(received)
			}
			for _, key := range []string{"callback_url", "draft", "draft_task", "edit", "extend", "seed", "duration"} {
				if _, ok := received[key]; ok {
					t.Fatal("unexpected field", key)
				}
			}
			content := received["content"].([]any)
			if len(content) != len(tc.roles)+1 {
				t.Fatal(content)
			}
			for i, role := range tc.roles {
				v := content[i+1].(map[string]any)
				if v["role"] != string(role) {
					t.Fatal(v)
				}
				typ := "image_url"
				if role == video.ReferenceVideo {
					typ = "video_url"
				}
				if role == video.ReferenceAudio {
					typ = "audio_url"
				}
				if v["type"] != typ || v[typ].(map[string]any)["url"] != "https://media.example/synthetic" {
					t.Fatal(v)
				}
			}
			if tc.name == "references" && received["omni_reference_task_type"] != "reference" {
				t.Fatal(received)
			}
		})
	}
}
func TestSubmit_InvalidBeforeNetwork(t *testing.T) {
	a := testAdapter(t, func(http.ResponseWriter, *http.Request) { t.Error("invalid request submitted") })
	for _, opts := range []map[string]any{{"frames": 30}, {"frames": 290}, {"frames": 29.5}, {"seed": 42}, {"callback_url": "https://x.example"}, {"draft": true}, {"draft_task": "cgt-x"}, {"omni_reference_task_type": "edit"}, {"extend": true}} {
		j := job()
		j.Request.ProviderOptions = opts
		if _, e := a.Submit(context.Background(), j, prepare(t, a, j)); e == nil {
			t.Fatal(opts)
		}
	}
	j := job()
	j.Request.Inputs = []video.Input{{Role: video.LastFrame, MediaType: "image/png", Source: video.Source{URL: "https://a.example/x"}}}
	if _, e := a.Submit(context.Background(), j, prepare(t, a, j)); e == nil {
		t.Fatal("last-only accepted")
	}
}
func TestSubmit_Seed1xAndInline(t *testing.T) {
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		var b map[string]any
		json.NewDecoder(r.Body).Decode(&b)
		if b["seed"] != float64(42) || b["duration"] != float64(5) {
			t.Error(b)
		}
		c := b["content"].([]any)
		if c[1].(map[string]any)["image_url"].(map[string]any)["url"] != "data:image/png;base64,AQID" {
			t.Error(c)
		}
		io.WriteString(w, `{"id":"cgt-test"}`)
	})
	j := job()
	j.ModelID = "seedance-1-5-pro-251215"
	j.Request.Model = "byteplus/" + j.ModelID
	j.Request.ProviderOptions = map[string]any{"seed": 42}
	j.Request.Inputs = []video.Input{{Role: video.FirstFrame, MediaType: "image/png", Source: video.Source{Data: "AQID"}}}
	if _, e := a.Submit(context.Background(), j, prepare(t, a, j)); e != nil {
		t.Fatal(e)
	}
}
func TestPoll_StatusesUsageAndSchema(t *testing.T) {
	for raw, want := range map[string]video.State{"queued": video.StateQueued, "running": video.StateRunning, "succeeded": video.StateCompleted, "failed": video.StateFailed, "expired": video.StateFailed, "cancelled": video.StateCancelled} {
		t.Run(raw, func(t *testing.T) {
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprintf(w, `{"id":"cgt-test","status":%q,"content":{"video_url":"https://cdn.example/video.mp4"},"duration":5,"usage":{"completion_tokens":123},"error":{"code":"InvalidParameter.TaskTypeConstraint","message":"synthetic"}}`, raw)
			})
			got, e := a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test"})
			if e != nil || got.Normalized != want || got.Usage.Lines[0].Quantity != 123 || got.Usage.Lines[0].Unit != video.VideoTokens || got.Error.Code != "InvalidParameter.TaskTypeConstraint" {
				t.Fatal(got, e)
			}
		})
	}
	for _, body := range []string{`{"status":"new_state"}`, `{bad`, `{}`, `{"status":42}`, `{"status":"succeeded"}`, `{"status":"queued","usage":{"completion_tokens":-1}}`, `{"status":"queued"} {}`} {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) })
		_, e := a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test"})
		var schema *video.SchemaError
		if !errors.As(e, &schema) {
			t.Fatal(body, e)
		}
	}
}
func TestHTTP_ErrorMapping(t *testing.T) {
	for _, status := range []int{401, 403, 429, 400, 500, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Retry-After", "7")
				w.Header().Set("X-Request-Id", "req-test")
				w.WriteHeader(status)
				io.WriteString(w, `{"error":{"code":"SensitiveProviderCode","message":"private-url?secret=test"}}`)
			})
			j := job()
			_, e := a.Submit(context.Background(), j, prepare(t, a, j))
			var up *video.UpstreamError
			if !errors.As(e, &up) {
				t.Fatal(e)
			}
			code := "submit_rejected"
			if status == 401 || status == 403 {
				code = "provider_access_denied"
			}
			if status == 429 {
				code = "provider_rate_limited"
			}
			if status >= 500 {
				code = "provider_unavailable"
			}
			if up.Code != code || up.Retryable != (status == 429 || status >= 500) || up.RequestID != "req-test" || strings.Contains(e.Error(), "secret") {
				t.Fatal(up)
			}
			if status == 429 && up.RetryAfter != 7*time.Second {
				t.Fatal(up.RetryAfter)
			}
		})
	}
}
func TestCancel_QueuedOnlyAndConfirm(t *testing.T) {
	for _, tc := range []struct {
		before, after string
		want          video.CancelState
		deletes       int32
	}{{"running", "running", video.CancelNotAvailable, 0}, {"succeeded", "succeeded", video.CancelNotAvailable, 0}, {"queued", "cancelled", video.CancelConfirmed, 1}, {"queued", "running", video.CancelNotAvailable, 1}, {"queued", "queued", video.CancelRequested, 1}} {
		t.Run(tc.before+"_"+tc.after, func(t *testing.T) {
			var calls atomic.Int32
			var polls atomic.Int32
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "DELETE" {
					calls.Add(1)
					w.WriteHeader(204)
					return
				}
				polls.Add(1)
				state := tc.before
				if calls.Load() > 0 {
					state = tc.after
				}
				fmt.Fprintf(w, `{"id":"cgt-test","status":%q,"content":{"video_url":"https://cdn.example/v.mp4"}}`, state)
			})
			got, e := a.Cancel(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", ProviderState: tc.before})
			if (tc.want != video.CancelNotAvailable && e != nil) || got.State != tc.want || calls.Load() != tc.deletes || got.ReleasesCharge {
				t.Fatal(got, e, calls.Load())
			}
			if tc.deletes > 0 && polls.Load() < 2 {
				t.Fatal("no preflight/confirmation poll")
			}
		})
	}
}
func TestFetch_GuardNoAuthAndCap(t *testing.T) {
	data := videotest.MP4()
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Error("provider credential leaked to CDN")
		}
		w.Write(data)
	})
	raw := strings.TrimSuffix(a.base.String(), "/api/v3") + "/media"
	ref := video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: raw, ContentType: "video/mp4"}}}
	r, _, e := a.Fetch(context.Background(), ref, 0)
	if e != nil {
		t.Fatal(e)
	}
	b, e := io.ReadAll(r)
	r.Close()
	if e != nil || !bytes.Equal(b, data) {
		t.Fatal(e)
	}
	a.maxDownloadBytes = 8
	r, _, e = a.Fetch(context.Background(), ref, 0)
	if e == nil {
		_, e = io.ReadAll(r)
		r.Close()
	}
	if e == nil {
		t.Fatal("oversize accepted")
	}
	a.download = newDownloadClient(a.cfg.Provider.Deadlines)
	ref.Outputs[0].URL = "https://169.254.169.254/latest"
	if _, _, e = a.Fetch(context.Background(), ref, 0); e == nil {
		t.Fatal("metadata allowed")
	}
}
func runContractSuite(t *testing.T, j *video.JobView) {
	var polls atomic.Int32
	var endpoint string
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/media" {
			w.Write(videotest.MP4())
			return
		}
		if r.Method == "POST" {
			io.WriteString(w, `{"id":"cgt-test"}`)
			return
		}
		state := "succeeded"
		if polls.Add(1) == 1 {
			state = "queued"
		}
		fmt.Fprintf(w, `{"id":"cgt-test","status":%q,"content":{"video_url":%q},"usage":{"completion_tokens":123}}`, state, endpoint+"/media")
	})
	endpoint = strings.TrimSuffix(a.base.String(), "/api/v3")
	videotest.RunContractSuite(t, a, videotest.Fixtures{Job: j, PollStates: []video.State{video.StateQueued, video.StateCompleted}, FetchBytes: videotest.MP4()})
}
func TestOfficialRetrieveFixture(t *testing.T) {
	body, e := os.ReadFile("testdata/retrieve-official.json")
	if e != nil {
		t.Fatal(e)
	}
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { w.Write(body) })
	got, e := a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "fixture"})
	if e != nil || got.Normalized != video.StateCompleted || got.Usage == nil || len(got.Outputs) != 1 {
		t.Fatal(got, e)
	}
}

func TestFetch_RefusalIsNotCallerCancellation(t *testing.T) {
	a := testAdapter(t, func(http.ResponseWriter, *http.Request) {})
	a.download = newDownloadClient(a.cfg.Provider.Deadlines)
	_, _, err := a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: "https://127.0.0.1/media"}}}, 0)
	if err == nil || errors.Is(err, context.Canceled) {
		t.Fatal("refusal misclassified", err)
	}
}
func TestHTTP_DeadlinesRedirectsAndLimits(t *testing.T) {
	t.Run("deadline", func(t *testing.T) {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
		a.cfg.Provider.Deadlines.Read = 25 * time.Millisecond
		start := time.Now()
		_, err := a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test"})
		if !errors.Is(err, context.DeadlineExceeded) || time.Since(start) > time.Second {
			t.Fatal(err)
		}
	})
	t.Run("no API redirects", func(t *testing.T) {
		var hits atomic.Int32
		target := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { hits.Add(1) }))
		defer target.Close()
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 307) })
		j := job()
		_, err := a.Submit(context.Background(), j, prepare(t, a, j))
		if err == nil || hits.Load() != 0 {
			t.Fatal(err, hits.Load())
		}
	})
	t.Run("bounded response", func(t *testing.T) {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
			io.WriteString(w, strings.Repeat(" ", maxResponseBytes+1))
		})
		_, err := a.Poll(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test"})
		var schema *video.SchemaError
		if !errors.As(err, &schema) {
			t.Fatal(err)
		}
	})
}
func TestFetch_StreamCapDeadlineAndDownloadLimit(t *testing.T) {
	t.Run("chunked cap", func(t *testing.T) {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
			w.(http.Flusher).Flush()
			w.Write(bytes.Repeat([]byte("x"), 100))
		})
		a.maxDownloadBytes = 10
		raw := strings.TrimSuffix(a.base.String(), "/api/v3")
		r, _, err := a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: raw}}}, 0)
		if err != nil {
			t.Fatal(err)
		}
		defer r.Close()
		b, err := io.ReadAll(r)
		if len(b) != 10 || err == nil {
			t.Fatal(len(b), err)
		}
	})
	t.Run("body deadline", func(t *testing.T) {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/warmup" {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			w.(http.Flusher).Flush()
			<-r.Context().Done()
		})
		raw := strings.TrimSuffix(a.base.String(), "/api/v3")
		// Establish TLS before testing the body deadline. Otherwise a busy race
		// run can spend the entire 25 ms on the handshake and never read a body.
		warmCtx, stopWarmup := context.WithTimeout(context.Background(), time.Second)
		defer stopWarmup()
		warmReq, err := http.NewRequestWithContext(warmCtx, http.MethodGet, raw+"/warmup", nil)
		if err != nil {
			t.Fatal(err)
		}
		warm, err := a.download.Do(warmReq)
		if err != nil {
			t.Fatal(err)
		}
		warm.Body.Close()
		if warm.StatusCode != http.StatusNoContent {
			t.Fatal(warm.StatusCode)
		}
		a.cfg.FetchTimeout = 25 * time.Millisecond
		r, _, err := a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: raw}}}, 0)
		if err != nil {
			t.Fatal(err)
		}
		defer r.Close()
		_, err = io.ReadAll(r)
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal(err)
		}
	})
	t.Run("download count", func(t *testing.T) {
		var hits atomic.Int32
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { hits.Add(1); w.WriteHeader(200) })
		a.downloads["cgt-test/0"] = downloadCount{count: 99, expires: time.Now().Add(time.Hour)}
		raw := strings.TrimSuffix(a.base.String(), "/api/v3")
		ref := video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: raw}}}
		r, _, err := a.Fetch(context.Background(), ref, 0)
		if err != nil {
			t.Fatal(err)
		}
		r.Close()
		if _, _, err = a.Fetch(context.Background(), ref, 0); err == nil {
			t.Fatal("101st download permitted")
		}
		if a.DownloadAttempts("cgt-test", 0) != 100 || hits.Load() != 1 {
			t.Fatal(hits.Load())
		}
	})
}
func TestCancel_CompletionBeforeDelete(t *testing.T) {
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "DELETE" {
			t.Error("deleted completed provider task")
		}
		io.WriteString(w, `{"id":"cgt-test","status":"succeeded","content":{"video_url":"https://cdn.example/video.mp4"}}`)
	})
	got, err := a.Cancel(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", ProviderState: "queued"})
	var unavailable *video.UpstreamError
	if !errors.As(err, &unavailable) || unavailable.Code != "cancel_not_available" || got.State != video.CancelNotAvailable {
		t.Fatal(got, err)
	}
}
func TestRetryAfter(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	for _, tc := range []struct {
		s    string
		want time.Duration
	}{{"", 0}, {"garbage", 0}, {"-1", 0}, {"0", 0}, {"5", 5 * time.Second}, {now.Add(10 * time.Second).Format(http.TimeFormat), 10 * time.Second}, {now.Add(-time.Second).Format(http.TimeFormat), 0}} {
		if got := retryAfter(tc.s, now); got != tc.want {
			t.Fatal(tc.s, got)
		}
	}
}

func TestContractSuite(t *testing.T) {
	for _, m := range capability.BytePlus().Models {
		for op, spec := range m.Operations {
			t.Run(m.ModelID+"/"+string(op), func(t *testing.T) {
				j := job()
				j.ModelID = m.ModelID
				j.Request.Model = "byteplus/" + m.ModelID
				roles := spec.RequiredRoles
				if op == video.Reference {
					roles = []video.Role{video.ReferenceImage}
				}
				for _, role := range roles {
					j.Request.Inputs = append(j.Request.Inputs, video.Input{Role: role, MediaType: "image/png", Source: video.Source{URL: "https://media.example/synthetic.png"}})
				}
				runContractSuite(t, j)
			})
		}
	}
}
