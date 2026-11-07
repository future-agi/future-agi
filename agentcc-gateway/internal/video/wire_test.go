package video

import (
	"testing"
	"time"
)

// OpenAI-shaped jobs map every durable state onto OpenAI's enum and always
// carry its nullable fields; native jobs keep the gateway vocabulary and only
// gain keys that have a value (completed_at stays absent until set).
func TestStatusResponseWireDialects(t *testing.T) {
	now := time.Unix(1793000000, 0)
	for _, tc := range []struct {
		status, openai string
		err            *VideoError
		code           any
		progress       int
	}{
		{StatusSubmitting, "queued", nil, nil, 0},
		{StatusSubmissionUnknown, "queued", nil, nil, 0},
		{StatusQueued, "queued", nil, nil, 0},
		{StatusRunning, "in_progress", nil, nil, 40},
		{StatusCompleted, "completed", nil, nil, 100},
		{StatusFailed, "failed", &VideoError{Code: "provider_expired", Message: "provider job expired before completion"}, "provider_expired", 0},
		{StatusCancelled, "failed", nil, "cancelled", 0},
	} {
		j := &VideoJob{ID: "video_x", Status: tc.status, Model: "openrouter/runway/gen-4.5", CreatedAt: now, Error: tc.err, Progress: 40, ProgressReported: tc.status == StatusRunning}
		if tc.status == StatusCompleted || tc.status == StatusFailed || tc.status == StatusCancelled {
			j.CompletedAt = &now
		}
		native := j.ToStatusResponse()
		if native["status"] != tc.status || native["gateway_status"] != nil {
			t.Fatalf("%s native: %v", tc.status, native)
		}
		if _, ok := native["completed_at"]; ok != (j.CompletedAt != nil) {
			t.Fatalf("%s native completed_at presence changed: %v", tc.status, native)
		}
		for _, k := range []string{"seconds", "size", "remixed_from_video_id"} {
			if _, ok := native[k]; ok {
				t.Fatalf("%s native gained empty %s", tc.status, k)
			}
		}
		j.Wire = WireOpenAI
		o := j.ToStatusResponse()
		if o["status"] != tc.openai || o["gateway_status"] != tc.status || o["progress"] != tc.progress {
			t.Fatalf("%s openai: %v", tc.status, o)
		}
		for _, k := range []string{"seconds", "size", "completed_at", "remixed_from_video_id", "error"} {
			if _, ok := o[k]; !ok {
				t.Fatalf("%s openai missing %s", tc.status, k)
			}
		}
		if tc.code == nil && o["error"] != nil || tc.code != nil && o["error"].(map[string]any)["code"] != tc.code {
			t.Fatalf("%s openai error: %v", tc.status, o["error"])
		}
	}
	j := &VideoJob{ID: "video_y", Status: StatusQueued, Duration: 4, Size: "854x480", CreatedAt: now}
	if r := j.ToStatusResponse(); r["seconds"] != "4" || r["size"] != "854x480" {
		t.Fatal(r)
	}
}
