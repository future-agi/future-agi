package lifecycle

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
)

func TestLogsAndTrace_NoPromptMediaKeysOrURLs(t *testing.T) {
	obs := completed()
	obs.Outputs[0].URL = "https://signed.example/video?sig=private-signature"
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(obs)}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	var logs, traces bytes.Buffer
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
	defer slog.SetDefault(old)
	reg := metrics.NewRegistry()
	f.s.SetTelemetry(reg, otel.NewStdoutExporter(&traces), "gateway-test")
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	for _, secret := range []string{"private prompt sentinel", "https://", "authorization", "Source", "correlation_token"} {
		if strings.Contains(logs.String()+traces.String(), secret) {
			t.Fatalf("leaked %q", secret)
		}
	}
	seen := map[string]bool{}
	traceID := ""
	decoder := json.NewDecoder(&traces)
	for decoder.More() {
		var span otel.Span
		if e := decoder.Decode(&span); e != nil {
			t.Fatal(e)
		}
		seen[span.Name] = true
		if traceID == "" {
			traceID = span.TraceID
		}
		if span.TraceID != traceID || traceID == "" {
			t.Fatal("trace not linked")
		}
		for _, k := range []string{"video_id", "org_id", "service", "model_id", "provider_job_id"} {
			if _, ok := span.Attributes[k]; !ok {
				t.Fatal("missing", k)
			}
		}
	}
	for _, name := range []string{"video.submit", "video.provider.submit", "video.provider.poll", "video.result.copy", "video.settle"} {
		if !seen[name] {
			t.Fatal("missing", name)
		}
	}
	if f.job(t, j.ID).TraceID != traceID {
		t.Fatal("trace ID not persisted")
	}
	if !strings.Contains(reg.Render(), `video_jobs_total{model="dreamina-seedance-2-5-260628",service="byteplus",status="completed"} 1`) {
		t.Fatal(reg.Render())
	}
}
func TestMetricsUnsettledAndUnknownCounters(t *testing.T) {
	f := setup(t, videotest.Script{Submit: videotest.Reply{Status: 500, Body: []byte("prompt-secret token-secret https://signed.example/?sig=secret")}})
	reg := metrics.NewRegistry()
	f.s.SetTelemetry(reg, nil, "")
	f.accept(t)
	f.tick(t)
	f.clock.Advance(time.Hour)
	f.tick(t)
	if e := f.s.RefreshMetrics(context.Background()); e != nil {
		t.Fatal(e)
	}
	out := reg.Render()
	for _, v := range []string{"video_submission_unknown_total 1", "video_unresolved_total 1", "video_unsettled_micros 1155600"} {
		if !strings.Contains(out, v) {
			t.Fatal(v, out)
		}
	}
}
