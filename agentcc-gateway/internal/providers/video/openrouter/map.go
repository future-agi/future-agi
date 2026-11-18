package openrouter

import (
	"encoding/json"
	"fmt"
	"math"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

// resolved re-validates the persisted canonical request against the pinned
// capability record before anything is sent upstream.
func (a *Adapter) resolved(j *video.JobView) (capability.Resolved, error) {
	if j == nil {
		return capability.Resolved{}, fmt.Errorf("video job required")
	}
	r := j.Request
	if r.Model == "" {
		r.Model = service + "/" + j.ModelID
	}
	if j.ModelID != "" && r.Model != service+"/"+j.ModelID {
		return capability.Resolved{}, fmt.Errorf("video model identity mismatch")
	}
	if j.Service != "" && j.Service != service || j.Region != "" && j.Region != a.cfg.Provider.Region || j.AccountRef != "" && a.cfg.Provider.AccountRef != "" && j.AccountRef != a.cfg.Provider.AccountRef {
		return capability.Resolved{}, fmt.Errorf("video provider identity mismatch")
	}
	res, e := capability.NewRegistry().Normalize(r, a.cfg.Provider.Region)
	if e != nil {
		return res, e
	}
	if len(a.cfg.Provider.Models) > 0 {
		found := false
		for _, m := range a.cfg.Provider.Models {
			found = found || m == res.ModelID
		}
		if !found {
			return res, fmt.Errorf("video model not configured")
		}
	}
	return res, nil
}

type imageURL struct {
	URL string `json:"url"`
}
type frameImage struct {
	Type      string   `json:"type"`
	ImageURL  imageURL `json:"image_url"`
	FrameType string   `json:"frame_type"`
}
type submitRequest struct {
	Model         string       `json:"model"`
	Prompt        string       `json:"prompt,omitempty"`
	Duration      int          `json:"duration"`
	Resolution    string       `json:"resolution"`
	AspectRatio   string       `json:"aspect_ratio"`
	GenerateAudio *bool        `json:"generate_audio,omitempty"`
	Seed          *int64       `json:"seed,omitempty"`
	FrameImages   []frameImage `json:"frame_images,omitempty"`
	User          string       `json:"user,omitempty"`
}

// submitBody maps the gateway contract onto OpenRouter's request:
// duration_seconds -> duration, audio -> generate_audio (only where the
// catalog documents the flag), first/last frame inputs -> frame_images as data
// URLs. Reference roles are not enabled for any pinned OpenRouter model.
func (a *Adapter) submitBody(j *video.JobView, user string) (submitRequest, error) {
	res, e := a.resolved(j)
	if e != nil {
		return submitRequest{}, e
	}
	r := res.Request
	body := submitRequest{Model: res.ModelID, Prompt: r.Prompt, Duration: int(r.DurationSeconds), Resolution: r.Resolution, AspectRatio: r.AspectRatio, User: user}
	if capability.OpenRouterSendsAudioFlag(res.ModelID) && r.Audio != nil {
		on := *r.Audio
		body.GenerateAudio = &on
	}
	if v, ok := r.ProviderOptions["seed"]; ok {
		n, ok := seed(v)
		if !ok {
			return submitRequest{}, &video.SchemaError{Field: "provider_options.seed"}
		}
		body.Seed = &n
	}
	for i, in := range r.Inputs {
		var frame string
		switch in.Role {
		case video.FirstFrame:
			frame = "first_frame"
		case video.LastFrame:
			frame = "last_frame"
		default:
			return submitRequest{}, &video.SchemaError{Field: fmt.Sprintf("inputs[%d].role", i)}
		}
		if in.Source.Data == "" {
			return submitRequest{}, &video.SchemaError{Field: fmt.Sprintf("inputs[%d].source", i)}
		}
		body.FrameImages = append(body.FrameImages, frameImage{Type: "image_url", ImageURL: imageURL{URL: "data:" + in.MediaType + ";base64," + in.Source.Data}, FrameType: frame})
	}
	return body, nil
}
func seed(v any) (int64, bool) {
	switch n := v.(type) {
	case int64:
		return n, true
	case int:
		return int64(n), true
	case float64:
		if math.Trunc(n) == n && n >= 0 && n <= math.MaxInt32 {
			return int64(n), true
		}
	case json.Number:
		i, e := n.Int64()
		return i, e == nil
	}
	return 0, false
}

type jobUsage struct {
	// Cost is USD. Absent or null means unknown, never free.
	Cost   *json.Number `json:"cost"`
	IsBYOK *bool        `json:"is_byok"`
}
type jobResponse struct {
	ID           string    `json:"id"`
	PollingURL   string    `json:"polling_url"`
	Status       string    `json:"status"`
	Error        string    `json:"error"`
	GenerationID string    `json:"generation_id"`
	UnsignedURLs []string  `json:"unsigned_urls"`
	URLs         []string  `json:"urls"`
	ExpiresAt    *int64    `json:"expires_at"`
	Usage        *jobUsage `json:"usage"`
}

func normalize(status string) (video.State, bool) {
	switch status {
	case "pending":
		return video.StateQueued, true
	case "in_progress":
		return video.StateRunning, true
	case "completed":
		return video.StateCompleted, true
	case "failed", "expired":
		return video.StateFailed, true
	case "cancelled":
		return video.StateCancelled, true
	}
	return "", false
}

// observation keeps only what recovery and settlement need. Provider error
// text, polling URLs and signed bearer download URLs are dropped here.
func (j jobResponse) observation(providerJobID string) (video.Observation, error) {
	if j.ID != "" && j.ID != providerJobID {
		return video.Observation{}, &video.SchemaError{Field: "id"}
	}
	state, ok := normalize(j.Status)
	if !ok {
		return video.Observation{}, &video.SchemaError{Field: "status"}
	}
	obs := video.Observation{ProviderState: j.Status, Normalized: state}
	switch j.Status {
	case "failed":
		obs.Error = &video.ProviderError{Code: "provider_failed", Message: "provider generation failed"}
	case "expired":
		obs.Error = &video.ProviderError{Code: "provider_expired", Message: "provider job expired before completion"}
	}
	if state == video.StateCompleted {
		n := len(j.UnsignedURLs)
		if n == 0 {
			n = 1
		}
		for i := 0; i < n; i++ {
			out := video.OutputRef{Handle: fmt.Sprint(i), ContentType: "video/mp4"}
			obs.Outputs = append(obs.Outputs, out)
		}
	}
	// With is_byok the model vendor bills the operator's own key directly and
	// usage.cost is only OpenRouter's share, so the job's total charge is not
	// observable here: it stays unsettled with the reservation held, for
	// operator reconciliation, instead of settling at a partial amount.
	if j.Usage != nil && j.Usage.Cost != nil && (j.Usage.IsBYOK == nil || !*j.Usage.IsBYOK) {
		cost, e := j.Usage.Cost.Float64()
		if e != nil || cost < 0 || math.IsNaN(cost) || math.IsInf(cost, 0) {
			return video.Observation{}, &video.SchemaError{Field: "usage.cost"}
		}
		obs.Usage = &video.Usage{Lines: []video.UsageLine{{Unit: video.USD, Quantity: cost, Source: video.UsageProviderReported, Currency: "USD"}}}
	}
	return obs, nil
}
