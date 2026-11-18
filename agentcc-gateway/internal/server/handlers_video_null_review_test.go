package server

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/video"
)

// TestReviewOpenAINullAudioRefusedBeforeAcceptance: lane flow (4). An
// explicit "audio": null in the OpenAI JSON shape is refused with 400 before
// any job or budget reservation exists; it is never read as audio=false.
func TestReviewOpenAINullAudioRefusedBeforeAcceptance(t *testing.T) {
	f := newVideoHTTP(t)
	for _, field := range []string{"audio", "size", "resolution", "aspect_ratio"} {
		body := fmt.Sprintf(`{"model":%q,"prompt":"test","seconds":"5",%q:null}`, videoTestModel, field)
		w := f.request("POST", "/v1/videos", f.keyA, video.NewID(), body)
		if w.Code != 400 || !strings.Contains(w.Body.String(), `"code":"invalid_parameter"`) || !strings.Contains(w.Body.String(), field) {
			t.Errorf("%s null got %d %s", field, w.Code, w.Body.String())
		}
	}
	jobs, err := f.store.ListAccounting(context.Background())
	if err != nil || len(jobs) != 0 {
		t.Fatalf("null field accepted %d jobs: %v", len(jobs), err)
	}
	keys, err := f.client.Redis().Keys(context.Background(), f.prefix+"reservation:*").Result()
	if err != nil || len(keys) != 0 {
		t.Fatalf("null field reserved: %v %v", keys, err)
	}
	// Omitting the field still uses the model default.
	w := f.request("POST", "/v1/videos", f.keyA, video.NewID(), fmt.Sprintf(`{"model":%q,"prompt":"test","seconds":"5"}`, videoTestModel))
	if w.Code != 202 {
		t.Fatalf("omitted audio refused %d %s", w.Code, w.Body.String())
	}
}
