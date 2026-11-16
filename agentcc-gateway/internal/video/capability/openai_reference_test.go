package capability

import (
	"errors"
	"testing"
)

func TestOpenAIReferenceRejectsCaseAliases(t *testing.T) {
	for _, ref := range []string{
		`{"image_url":"https://media.example/first.png","IMAGE_URL":"https://media.example/second.png"}`,
		`{"IMAGE_URL":"https://media.example/first.png","image_url":"https://media.example/second.png"}`,
		`{"Image_Url":"https://media.example/first.png"}`,
		`{"image_url":"https://media.example/first.png","FILE_ID":""}`,
	} {
		t.Run(ref, func(t *testing.T) {
			_, err := DecodeOpenAIJSON([]byte(`{"model":"m","seconds":"4","input_reference":` + ref + `}`))
			var invalid *ValidationError
			if !errors.As(err, &invalid) || invalid.Code != "invalid_parameter" || invalid.Param != "input_reference" {
				t.Fatalf("case-aliased reference accepted or wrong validation error: %v", err)
			}
		})
	}
}

func TestOpenAIReferenceAcceptsCanonicalKeys(t *testing.T) {
	for _, ref := range []string{
		`{"image_url":"https://media.example/first.png"}`,
		`{"image_url":"https://media.example/first.png","file_id":""}`,
	} {
		c, err := DecodeOpenAIJSON([]byte(`{"model":"m","seconds":"4","input_reference":` + ref + `}`))
		if err != nil || c.InputReference == nil || c.InputReference.Source.URL != "https://media.example/first.png" {
			t.Fatalf("canonical reference rejected: %v", err)
		}
	}
}
