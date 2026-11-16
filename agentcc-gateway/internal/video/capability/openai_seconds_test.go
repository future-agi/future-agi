package capability

import (
	"fmt"
	"testing"
)

func TestReviewOpenAISecondsPresence(t *testing.T) {
	for _, value := range []string{"null", `""`, `" "`, "0", `"0"`, "-1", "1.5"} {
		c, err := DecodeOpenAIJSON([]byte(fmt.Sprintf(`{"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"test","seconds":%s}`, value)))
		if err == nil {
			_, err = c.Request()
		}
		if err == nil {
			t.Errorf("explicit seconds %s accepted", value)
		}
	}
	c, err := DecodeOpenAIJSON([]byte(`{"model":"openrouter/bytedance/seedance-1-5-pro","prompt":"test"}`))
	if err != nil {
		t.Fatal(err)
	}
	req, err := c.Request()
	if err != nil {
		t.Fatal(err)
	}
	normalized, err := NewRegistry().Normalize(req, "global")
	if err != nil || normalized.Request.DurationSeconds != 5 {
		t.Fatalf("omitted default: %+v %v", normalized, err)
	}
}
