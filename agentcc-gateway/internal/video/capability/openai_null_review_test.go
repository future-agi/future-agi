package capability

import (
	"errors"
	"fmt"
	"testing"
)

const nullTestModel = "openrouter/bytedance/seedance-1-5-pro" // audio on by default

func openAIJSONNormalized(body string) (Request, error) {
	c, err := DecodeOpenAIJSON([]byte(body))
	if err != nil {
		return Request{}, err
	}
	req, err := c.Request()
	if err != nil {
		return Request{}, err
	}
	n, err := NewRegistry().Normalize(req, "global")
	if err != nil {
		return Request{}, err
	}
	return n.Request, nil
}

// TestReviewOpenAIJSONNullRefused: in the OpenAI JSON shape an explicit null
// must be refused with 400 invalid_parameter. A null audio used to unmarshal
// as false and silently turn the model's default audio off.
func TestReviewOpenAIJSONNullRefused(t *testing.T) {
	for _, field := range []string{"audio", "size", "resolution", "aspect_ratio", "prompt", "model", "input_reference", "seconds"} {
		body := fmt.Sprintf(`{"model":%q,"prompt":"test","seconds":"4",%q:null}`, nullTestModel, field)
		if field == "model" || field == "prompt" || field == "seconds" {
			body = fmt.Sprintf(`{"model":%q,"prompt":"test","size":"854x480",%q:null}`, nullTestModel, field)
			if field == "model" {
				body = `{"prompt":"test","size":"854x480","model":null}`
			}
			if field == "prompt" {
				body = fmt.Sprintf(`{"model":%q,"size":"854x480","prompt":null}`, nullTestModel)
			}
		}
		req, err := openAIJSONNormalized(body)
		var ve *ValidationError
		if !errors.As(err, &ve) || ve.Code != "invalid_parameter" || ve.Param != field {
			t.Errorf("%s: null accepted or wrong error: %+v (audio=%v)", field, err, req.Audio)
		}
	}
}

// TestReviewOpenAIJSONAudioValues: omitted audio keeps the model default;
// explicit booleans are honoured.
func TestReviewOpenAIJSONAudioValues(t *testing.T) {
	for _, tc := range []struct {
		audio string
		want  bool
	}{{"", true}, {`,"audio":true`, true}, {`,"audio":false`, false}} {
		req, err := openAIJSONNormalized(fmt.Sprintf(`{"model":%q,"prompt":"test","seconds":"4"%s}`, nullTestModel, tc.audio))
		if err != nil || req.Audio == nil || *req.Audio != tc.want {
			t.Errorf("audio %q: %+v %v", tc.audio, req.Audio, err)
		}
	}
}
