package capability

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"math"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"
)

func integer(v any) (int64, bool) {
	var f float64
	switch n := v.(type) {
	case int:
		return int64(n), true
	case int64:
		return n, true
	case float64:
		f = n
	case json.Number:
		var err error
		f, err = strconv.ParseFloat(string(n), 64)
		if err != nil {
			return 0, false
		}
	default:
		return 0, false
	}
	if math.IsNaN(f) || math.IsInf(f, 0) || math.Trunc(f) != f || f >= math.MaxInt64 || f < math.MinInt64 {
		return 0, false
	}
	return int64(f), true
}
func validateResolved(res Resolved) error {
	r, m := res.Request, res.Capability
	if res.Operation == video.TextToVideo && strings.TrimSpace(r.Prompt) == "" {
		return invalid("invalid_parameter", "prompt", "text-to-video prompt required")
	}
	d := r.DurationSeconds
	if math.IsNaN(d) || math.IsInf(d, 0) || math.Trunc(d) != d {
		return invalid("invalid_parameter", "duration_seconds", "finite integer required")
	}
	if d == -1 {
		if m.AutoDuration == nil || !m.AutoDuration.Bounded || m.Durations.Max <= 0 {
			return invalid("unsupported_combination", "duration_seconds", "auto duration is not bounded")
		}
	} else if d < float64(m.Durations.Min) || d > float64(m.Durations.Max) || len(m.Durations.Enum) > 0 && !contains(m.Durations.Enum, int(d)) {
		return invalid("invalid_parameter", "duration_seconds", "outside model range")
	}
	if len(r.Prompt) > m.PromptLimit.Max {
		return invalid("invalid_parameter", "prompt", "exceeds byte limit")
	}
	if !contains(m.Resolutions, r.Resolution) {
		return invalid("unsupported_combination", "resolution", "unsupported resolution")
	}
	ratios := m.AspectRatios
	if spec := m.Operations[res.Operation]; len(spec.AspectRatios) > 0 {
		ratios = spec.AspectRatios
	}
	if !contains(ratios, r.AspectRatio) {
		return invalid("unsupported_combination", "aspect_ratio", "unsupported ratio for operation")
	}
	if !contains(m.FPS, r.FPS) {
		return invalid("invalid_parameter", "fps", "unsupported fps")
	}
	if r.N < 1 || r.N > m.MaxOutputs || (r.N != 1 && !m.NativeMultiOutput) {
		return invalid("unsupported_combination", "n", "native multi-output required")
	}
	if r.Audio != nil && *r.Audio && m.Audio.Generated == "never" {
		return invalid("unsupported_combination", "audio", "generated audio unsupported")
	}
	for k, v := range r.ProviderOptions {
		spec, ok := m.Options[k]
		param := "provider_options." + k
		if !ok {
			return invalid("unknown_provider_option", param, "unknown provider option")
		}
		valid := false
		switch spec.Type {
		case "bool":
			_, valid = v.(bool)
		case "enum":
			s, ok := v.(string)
			valid = ok && contains(spec.Values, s)
		case "int":
			n, ok := integer(v)
			valid = ok && n >= spec.Min && n <= spec.Max && (spec.Step == 0 || (n-spec.Offset)%spec.Step == 0)
		}
		if !valid {
			return invalid("invalid_parameter", param, "invalid option value")
		}
	}
	counts := map[video.Role]int{}
	var bytes int64
	for i, in := range r.Inputs {
		param := fmt.Sprintf("inputs[%d]", i)
		spec, ok := m.Inputs[in.Role]
		if !ok || !contains(m.Operations[res.Operation].Roles, in.Role) {
			return invalid("unsupported_combination", param+".role", "unsupported role")
		}
		counts[in.Role]++
		if counts[in.Role] > spec.MaxCount {
			return invalid("invalid_parameter", param+".role", "too many inputs")
		}
		family, format, ok := strings.Cut(in.MediaType, "/")
		want := "image"
		if in.Role == video.ReferenceVideo {
			want = "video"
		}
		if in.Role == video.ReferenceAudio {
			want = "audio"
		}
		if !ok || family != want || !contains(spec.Formats, format) {
			return invalid("unsupported_combination", param+".media_type", "unsupported media type")
		}
		if (in.Source.URL == "") == (in.Source.Data == "") {
			return invalid("invalid_parameter", param+".source", "exactly one source required")
		}
		size := in.Bytes
		if in.Source.URL != "" {
			u, err := url.Parse(in.Source.URL)
			if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil {
				return invalid("invalid_parameter", param+".source.url", "https URL without userinfo required")
			}
		} else {
			b, err := base64.StdEncoding.DecodeString(in.Source.Data)
			if err != nil {
				return invalid("invalid_parameter", param+".source.data", "invalid base64")
			}
			size = int64(len(b))
		}
		if size < 0 || spec.MaxBytes > 0 && size >= spec.MaxBytes {
			return invalid("invalid_parameter", param, "media too large")
		}
		bytes += size
		if in.Width > 0 && in.Height > 0 && spec.MinDimension > 0 {
			ratio := float64(in.Width) / float64(in.Height)
			if in.Width < spec.MinDimension || in.Height < spec.MinDimension || in.Width > spec.MaxDimension || in.Height > spec.MaxDimension || ratio <= spec.MinRatio || ratio >= spec.MaxRatio || int64(in.Width)*int64(in.Height) > spec.MaxPixels {
				return invalid("invalid_parameter", param, "image dimensions outside model range")
			}
		}
	}
	if bytes > 64*1024*1024 {
		return invalid("invalid_parameter", "inputs", "request media exceeds 64 MiB")
	}
	for _, role := range m.Operations[res.Operation].RequiredRoles {
		if counts[role] == 0 {
			return invalid("unsupported_combination", "inputs", "required role missing")
		}
	}
	if counts[video.ReferenceAudio] > 0 && counts[video.ReferenceImage] == 0 && counts[video.ReferenceVideo] == 0 && !m.Audio.AudioOnly {
		return invalid("unsupported_combination", "inputs", "audio requires image or video")
	}
	if len(r.Metadata) > 16 {
		return invalid("invalid_parameter", "metadata", "at most 16 keys")
	}
	for k, v := range r.Metadata {
		if utf8.RuneCountInString(k) > 64 || utf8.RuneCountInString(v) > 512 {
			return invalid("invalid_parameter", "metadata", "metadata length limit")
		}
	}
	if utf8.RuneCountInString(r.EndUserID) > 128 {
		return invalid("invalid_parameter", "end_user_id", "length limit")
	}
	return nil
}
