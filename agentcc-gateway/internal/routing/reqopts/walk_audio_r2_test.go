package reqopts

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestWalkManagedMessageAudioR2(t *testing.T) {
	for _, audio := range []string{`{}`, `{"id":"audio_fixture"}`, `{"id":"audio_fixture","data":"SGVsbG8=","transcript":"Hello"}`} {
		for _, content := range []string{`null`, `"previous answer"`} {
			t.Run(audio+"/"+content, func(t *testing.T) {
				var req models.ChatCompletionRequest
				if err := json.Unmarshal([]byte(`{"model":"text","messages":[{"role":"user","content":"hello"},{"role":"assistant","content":`+content+`,"audio":`+audio+`}]}`), &req); err != nil {
					t.Fatal(err)
				}
				if req.Messages[1].Audio == nil {
					t.Fatal("fixture lost audio before admission")
				}
				err := CheckManaged(&req, &models.RoutingOptions{}, nil)
				if err == nil || err.Status != 400 || err.Code != CodeUnsupportedFieldForManaged || err.Param == nil || *err.Param != "messages[1].audio" {
					t.Errorf("managed audio was not refused at its message path: %+v", err)
				}
				removed := StripForManaged(&req, nil)
				if !reflect.DeepEqual(removed, []string{"messages[1].audio"}) || req.Messages[1].Audio != nil {
					t.Errorf("managed fallback retained audio: removed=%v audio=%+v", removed, req.Messages[1].Audio)
				}
				if string(req.Messages[1].Content) != content || string(req.Messages[0].Content) != `"hello"` {
					t.Fatal("stripping audio changed message content")
				}
				if err := CheckManaged(&req, &models.RoutingOptions{}, nil); err != nil {
					t.Fatalf("stripped request still refused: %v", err)
				}
			})
		}
	}
}
