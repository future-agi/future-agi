package metrics

import (
	"strings"
	"testing"
)

func TestVideoMetricsRegisteredAndLabelAllowlist(t *testing.T) {
	r := NewRegistry()
	names := []string{"video_jobs_total", "video_state_duration_seconds", "video_poll_requests_total", "video_submission_unknown_total", "video_unresolved_total", "video_result_copy_failures_total", "video_reserved_micros", "video_unsettled_micros", "video_queue_age_seconds", "video_lease_takeovers_total"}
	for _, n := range names {
		if !strings.Contains(r.Render(), "# TYPE "+n+" ") {
			t.Errorf("missing %s", n)
		}
	}
	r.VideoEvent("video_jobs_total", map[string]string{"service": "byteplus", "model": "dreamina-seedance-2-5-260628", "status": "queued", "org_id": "secret-org", "prompt": "secret-prompt"})
	r.VideoEvent("video_jobs_total", map[string]string{"service": "secret-service", "model": "secret-model", "status": "secret-state"})
	text := r.Render()
	if strings.Contains(text, "secret-") || strings.Contains(text, "org_id=") || strings.Contains(text, "prompt=") {
		t.Fatal(text)
	}
	r.VideoBalances(123, 45)
	if !strings.Contains(r.Render(), "video_reserved_micros 123\n") || !strings.Contains(r.Render(), "video_unsettled_micros 45\n") {
		t.Fatal(r.Render())
	}
}
