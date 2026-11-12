package byteplus

import (
	"fmt"
	"strings"
	"time"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
)

func (a *Adapter) resolved(j *video.JobView) (capability.Resolved, error) {
	if j == nil {
		return capability.Resolved{}, fmt.Errorf("video job required")
	}
	r := j.Request
	if r.Model == "" {
		r.Model = "byteplus/" + j.ModelID
	}
	if j.ModelID != "" && r.Model != "byteplus/"+j.ModelID {
		return capability.Resolved{}, fmt.Errorf("video model identity mismatch")
	}
	if j.Service != "" && j.Service != "byteplus" || j.Region != "" && j.Region != a.cfg.Provider.Region || j.AccountRef != "" && a.cfg.Provider.AccountRef != "" && j.AccountRef != a.cfg.Provider.AccountRef {
		return capability.Resolved{}, fmt.Errorf("video provider identity mismatch")
	}
	if strings.Contains(r.Model, "seedance-2-5-") {
		for _, in := range r.Inputs {
			if in.Role == video.FirstFrame || in.Role == video.LastFrame {
				r.AspectRatio = "adaptive"
			}
		}
	}
	res, e := capability.NewRegistry().Normalize(r, a.cfg.Provider.Region)
	if e != nil {
		return res, e
	}
	if len(a.cfg.Provider.Models) > 0 {
		found := false
		for _, m := range a.cfg.Provider.Models {
			if m == res.ModelID {
				found = true
				break
			}
		}
		if !found {
			return res, fmt.Errorf("video model not configured")
		}
	}
	return res, nil
}
func (a *Adapter) submitBody(j *video.JobView, token string) (map[string]any, error) {
	res, e := a.resolved(j)
	if e != nil {
		return nil, e
	}
	r := res.Request
	content := []map[string]any{}
	if r.Prompt != "" {
		content = append(content, map[string]any{"type": "text", "text": r.Prompt})
	}
	hasVideo := false
	for _, in := range r.Inputs {
		kind := "image_url"
		if in.Role == video.ReferenceVideo {
			kind = "video_url"
			hasVideo = true
		}
		if in.Role == video.ReferenceAudio {
			kind = "audio_url"
		}
		raw := in.Source.URL
		if raw != "" {
			if e := media.ValidateURL(raw); e != nil {
				return nil, e
			}
		} else {
			raw = "data:" + in.MediaType + ";base64," + in.Source.Data
		}
		content = append(content, map[string]any{"type": kind, kind: map[string]string{"url": raw}, "role": string(in.Role)})
	}
	b := map[string]any{"model": res.ModelID, "content": content, "resolution": r.Resolution, "ratio": r.AspectRatio, "duration": int(r.DurationSeconds), "generate_audio": *r.Audio, "safety_identifier": token, "execution_expires_after": a.cfg.Provider.ExecutionExpiresAfter}
	// Only the pinned allowlist can become wire fields. Normalize validates types,
	// frames arithmetic and the 1.x-only seed capability before any network call.
	for _, key := range []string{"watermark", "camera_fixed", "return_last_frame", "service_tier", "frames", "seed"} {
		if v, ok := r.ProviderOptions[key]; ok {
			b[key] = v
		}
	}
	if _, ok := b["frames"]; ok {
		delete(b, "duration")
	}
	if hasVideo {
		b["omni_reference_task_type"] = "reference"
	}
	return b, nil
}

type taskResponse struct {
	ID               string `json:"id"`
	Model            string `json:"model"`
	Status           string `json:"status"`
	SafetyIdentifier string `json:"safety_identifier"`
	CreatedAt        int64  `json:"created_at"`
	UpdatedAt        int64  `json:"updated_at"`
	Content          struct {
		VideoURL     string `json:"video_url"`
		LastFrameURL string `json:"last_frame_url"`
	} `json:"content"`
	Usage *struct {
		CompletionTokens *int64 `json:"completion_tokens"`
	} `json:"usage"`
	Error *struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
	Duration     int    `json:"duration"`
	Frames       int    `json:"frames"`
	FPS          int    `json:"framespersecond"`
	Resolution   string `json:"resolution"`
	Ratio        string `json:"ratio"`
	Seed         *int64 `json:"seed"`
	OutputFormat string `json:"output_format"`
}

func normalizeState(raw string) (video.State, error) {
	switch raw {
	case "queued":
		return video.StateQueued, nil
	case "running":
		return video.StateRunning, nil
	case "succeeded":
		return video.StateCompleted, nil
	case "failed", "expired":
		return video.StateFailed, nil
	case "cancelled":
		return video.StateCancelled, nil
	}
	return "", &video.SchemaError{Field: "status"}
}
func (t taskResponse) observation() (video.Observation, error) {
	state, e := normalizeState(t.Status)
	if e != nil {
		return video.Observation{}, e
	}
	obs := video.Observation{ProviderState: t.Status, Normalized: state}
	if t.Usage != nil {
		if t.Usage.CompletionTokens == nil || *t.Usage.CompletionTokens < 0 {
			return obs, &video.SchemaError{Field: "usage.completion_tokens"}
		}
		obs.Usage = &video.Usage{Lines: []video.UsageLine{{Unit: video.VideoTokens, Quantity: float64(*t.Usage.CompletionTokens), Source: video.UsageProviderReported, TariffRevision: capability.BytePlusRevision}}}
	}
	if t.Error != nil {
		obs.Error = &video.ProviderError{Code: t.Error.Code, Message: t.Error.Message}
	}
	if state == video.StateCompleted {
		if t.Content.VideoURL == "" || media.ValidateURL(t.Content.VideoURL) != nil {
			return obs, &video.SchemaError{Field: "content.video_url"}
		}
		typ := "video/mp4"
		if t.OutputFormat == "mov" {
			typ = "video/quicktime"
		}
		duration := float64(t.Duration)
		if duration == 0 && t.Frames > 0 && t.FPS > 0 {
			duration = float64(t.Frames) / float64(t.FPS)
		}
		if duration < 0 {
			return obs, &video.SchemaError{Field: "duration"}
		}
		out := video.OutputRef{URL: t.Content.VideoURL, ContentType: typ, DurationSeconds: duration}
		if t.UpdatedAt > 0 {
			expiry := time.Unix(t.UpdatedAt, 0).Add(24 * time.Hour)
			out.ExpiresAt = &expiry
		}
		obs.Outputs = []video.OutputRef{out}
	}
	return obs, nil
}
