package otel

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// These opaque fixtures deliberately do not match the configured text patterns.
func c26R2Opaque() string {
	return base64.StdEncoding.EncodeToString([]byte("audio-" + c26Marker()))
}

func c26R2Media(t *testing.T, sink string) (models.Message, string) {
	t.Helper()
	msg := models.Message{Role: "assistant"}
	opaque := c26R2Opaque()
	switch sink {
	case "input_audio":
		msg.Content = c26JSON(t, []any{map[string]any{
			"type": "input_audio", "input_audio": map[string]any{"data": opaque, "format": "wav"},
		}})
	case "audio_data":
		msg.Audio = &models.MessageAudio{Data: opaque, Transcript: c26Marker(), ExpiresAt: 123456}
	case "audio_id":
		opaque = "audio-" + "reference-" + base64.RawURLEncoding.EncodeToString([]byte(c26Marker()))
		msg.Audio = &models.MessageAudio{ID: opaque}
	case "generated_images":
		opaque = "https://" + "media.example.invalid/" + base64.RawURLEncoding.EncodeToString([]byte(c26Marker()))
		// Images is provider JSON, not necessarily a typed image content part.
		msg.Images = c26JSON(t, []any{map[string]any{"url": opaque}})
	default:
		t.Fatalf("unknown media fixture %q", sink)
	}
	return msg, opaque
}

func c26R2Place(rc *models.RequestContext, direction string, msg models.Message) string {
	if direction == "input" {
		msg.Role = "user"
		rc.Request.Messages = []models.Message{msg, msg}
		rc.Response = nil
		return "input.value"
	}
	rc.Request = nil
	rc.Response.Choices = []models.Choice{{Message: msg}, {Message: msg}}
	rc.IsStream = direction == "stream"
	return "output.value"
}

func c26R2Messages(t *testing.T, key, body string) []models.Message {
	t.Helper()
	if key == "input.value" {
		var request models.ChatCompletionRequest
		if err := json.Unmarshal([]byte(body), &request); err != nil {
			t.Fatal(err)
		}
		return request.Messages
	}
	var response models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(body), &response); err != nil {
		t.Fatal(err)
	}
	messages := make([]models.Message, len(response.Choices))
	for i, choice := range response.Choices {
		messages[i] = choice.Message
	}
	return messages
}

func TestC26R2OpaqueMedia(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone, "absent"} {
			for _, direction := range []string{"input", "output", "stream"} {
				for _, sink := range []string{"input_audio", "audio_data", "audio_id", "generated_images"} {
					t.Run(source+"/"+mode+"/"+direction+"/"+sink, func(t *testing.T) {
						for _, retention := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
							p, _ := bodyPlugin(t, true)
							t.Cleanup(p.Close)
							rc := newTestRC()
							rc.Privacy.ContentMode = retention
							msg, opaque := c26R2Media(t, sink)
							key := c26R2Place(rc, direction, msg)
							c26Policy(p, rc, source, mode)
							before := c26Snapshot(t, rc)
							wantIn, wantOut := bodyJSON(rc)
							want := wantOut
							if key == "input.value" {
								want = wantIn
							}
							attrs := c26Export(t, p, rc)
							value, ok := attrs[key].(string)
							if !ok {
								t.Fatalf("%s: missing %s", retention, key)
							}
							active := mode == privacy.ModePatterns || mode == privacy.ModeFull
							if active {
								if bytes.Contains(c26JSON(t, attrs), []byte(opaque)) {
									t.Errorf("%s: opaque %s survived in the exported span", retention, sink)
								}
								if mode == privacy.ModeFull && value != "[REDACTED]" {
									t.Errorf("%s: full body masking changed", retention)
								}
								if mode == privacy.ModePatterns {
									messages := c26R2Messages(t, key, value)
									if len(messages) != 2 {
										t.Fatal("lost messages or choices")
									}
									for _, message := range messages {
										switch sink {
										case "input_audio":
											var parts []struct {
												InputAudio map[string]string `json:"input_audio"`
											}
											if err := json.Unmarshal(message.Content, &parts); err != nil || len(parts) != 1 {
												t.Fatal("input audio part shape changed")
											}
											if parts[0].InputAudio["data"] != "[REDACTED]" || parts[0].InputAudio["format"] != "wav" {
												t.Error("input audio suppression changed format metadata")
											}
										case "generated_images":
											if len(message.Images) != 0 {
												t.Error("opaque generated images were retained")
											}
										case "audio_data":
											if message.Audio == nil || message.Audio.Data != "[REDACTED]" || message.Audio.ID != "" || message.Audio.ExpiresAt != 123456 {
												t.Fatal("audio data suppression changed absence or expiry metadata")
											}
											c26Masked(t, message.Audio.Transcript)
										case "audio_id":
											if message.Audio == nil || message.Audio.ID != "[REDACTED]" || message.Audio.Data != "" {
												t.Fatal("ID-only audio reference was retained or invented data")
											}
										}
									}
								}
							} else if value != want {
								t.Errorf("%s: disabled body bytes changed", retention)
							}
							if !active && !strings.Contains(value, opaque) {
								t.Errorf("%s: disabled media content disappeared", retention)
							}
							if rc.Request == nil {
								if _, exists := attrs["input.value"]; exists {
									t.Error("response-only export invented input")
								}
							}
							c26Metadata(t, attrs, rc)
							if !bytes.Equal(before, c26Snapshot(t, rc)) {
								t.Error("live messages, aliased audio, images or retention policy mutated")
							}
						}
					})
				}
			}
		}
	}
}

func TestC26R2InputAudioShapes(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, shape := range []string{"object", "nested", "untyped", "tool_string", "native_tool_string", "arguments"} {
			t.Run(source+"/"+shape, func(t *testing.T) {
				part := map[string]any{"type": "input_audio", "input_audio": map[string]any{"data": c26R2Opaque(), "format": "wav"}}
				var content any = part
				switch shape {
				case "nested":
					content = map[string]any{"result": []any{part}, "count": json.Number("9007199254740993")}
				case "untyped":
					delete(part, "type")
				case "tool_string":
					content = string(c26JSON(t, part))
				case "native_tool_string":
					content = map[string]any{"type": "tool_result", "content": string(c26JSON(t, part))}
				}
				p, _ := bodyPlugin(t, true)
				t.Cleanup(p.Close)
				rc := newTestRC()
				rc.Privacy.ContentMode = models.ContentModeFull
				msg := models.Message{Role: "tool", Content: c26JSON(t, content)}
				if shape == "arguments" {
					msg.Role = "assistant"
					msg.Content = nil
					msg.ToolCalls = []models.ToolCall{{ID: "call", Type: "function", Function: models.FunctionCall{Name: "audio", Arguments: string(c26JSON(t, part))}}}
				}
				rc.Request.Messages = []models.Message{msg}
				rc.Response.Choices = []models.Choice{{Message: msg}}
				c26Policy(p, rc, source, privacy.ModePatterns)
				before := c26Snapshot(t, rc)
				attrs := c26Export(t, p, rc)
				if bytes.Contains(c26JSON(t, attrs), []byte(c26R2Opaque())) {
					t.Error("input_audio survived in body or fallback message attribute")
				}
				if shape == "nested" {
					for _, key := range []string{"input.value", "output.value"} {
						if !strings.Contains(attrs[key].(string), "9007199254740993") {
							t.Error("unrelated exact integer changed during audio traversal")
						}
					}
				}
				if !bytes.Equal(before, c26Snapshot(t, rc)) {
					t.Error("live nested content changed")
				}
			})
		}
	}
}

func TestC26R2LiteralAudioTextControls(t *testing.T) {
	literal := `{"input_audio":{"data":"example-` + `only"}}`
	for _, shape := range []string{"plain", "text_block"} {
		t.Run(shape, func(t *testing.T) {
			for _, retention := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
				p, _ := bodyPlugin(t, true)
				t.Cleanup(p.Close)
				rc := newTestRC()
				rc.Privacy.ContentMode = retention
				msg := models.Message{Role: "user", Content: c26JSON(t, literal)}
				suffix := ".message.content"
				if shape == "text_block" {
					msg.Content = c26JSON(t, []any{map[string]any{"type": "text", "text": literal}})
					suffix = ".message.contents.0.message_content.text"
				}
				rc.Request.Messages = []models.Message{msg}
				rc.Response.Choices = []models.Choice{{Message: msg}}
				c26Policy(p, rc, "global", privacy.ModePatterns)
				before := c26Snapshot(t, rc)
				attrs := c26Export(t, p, rc)
				for _, side := range []string{"input", "output"} {
					if attrs["llm."+side+"_messages.0"+suffix] != literal {
						t.Error("ordinary quoted JSON was reinterpreted as media")
					}
					messages := c26R2Messages(t, side+".value", attrs[side+".value"].(string))
					if len(messages) != 1 || !bytes.Equal(messages[0].Content, msg.Content) {
						t.Error("ordinary body text changed")
					}
				}
				if !bytes.Equal(before, c26Snapshot(t, rc)) {
					t.Error("live literal content changed")
				}
			}
		})
	}
}

func TestC26R2UnknownMessageContent(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone, "absent"} {
			for _, direction := range []string{"input", "output"} {
				t.Run(source+"/"+mode+"/"+direction, func(t *testing.T) {
					for _, retention := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
						for _, raw := range []string{
							`{"opaque":"` + c26R2Opaque() + `","value":"` + c26EscapedMarker() + `"}`,
							`[7,{"opaque":"` + c26R2Opaque() + `"}]`,
						} {
							p, _ := bodyPlugin(t, true)
							t.Cleanup(p.Close)
							rc := newTestRC()
							rc.Privacy.ContentMode = retention
							c26R2Place(rc, direction, models.Message{Role: "assistant", Content: json.RawMessage(raw)})
							c26Policy(p, rc, source, mode)
							before := c26Snapshot(t, rc)
							attrs := c26Export(t, p, rc)
							for i := 0; i < 2; i++ {
								key := "llm." + direction + "_messages." + strconv.Itoa(i) + ".message.content"
								want := raw
								if mode == privacy.ModeFull || mode == privacy.ModePatterns {
									want = "[REDACTED]"
								}
								if attrs[key] != want {
									t.Errorf("%s: unexpected unknown-content export at %s", retention, key)
								}
							}
							if !bytes.Equal(before, c26Snapshot(t, rc)) {
								t.Error("live unknown content changed")
							}
						}
					}
				})
			}
		}
	}
}

func TestC26R2BodyFieldNames(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone, "absent"} {
			for _, include := range []bool{false, true} {
				t.Run(source+"/"+mode+"/bodies_"+strconv.FormatBool(include), func(t *testing.T) {
					for _, retention := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
						p, _ := bodyPlugin(t, include)
						t.Cleanup(p.Close)
						rc := newTestRC()
						rc.Privacy.ContentMode = retention
						// Sorted order is sensitive name, categorical-lookalike, opaque name.
						names := []string{c26Marker(), "field_0", "unmatched-" + c26R2Opaque()[:16]}
						values := []any{c26Marker(), false, float64(7)}
						rc.CallerExtras = map[string]any{names[2]: values[2], names[0]: values[0], names[1]: values[1]}
						c26Policy(p, rc, source, mode)
						before := c26Snapshot(t, rc)
						attrs := c26Export(t, p, rc)
						active := mode == privacy.ModeFull || mode == privacy.ModePatterns
						count := 0
						for key := range attrs {
							if strings.HasPrefix(key, bodyAttrPrefix) {
								count++
							}
							if active && (strings.Contains(key, names[0]) || strings.Contains(key, names[2])) {
								t.Error("caller field name exported raw")
							}
						}
						if count != len(names) {
							t.Error("masked names collided or lost a body field")
						}
						for i, name := range names {
							key := bodyAttrPrefix + name
							want := values[i]
							if active {
								key = bodyAttrPrefix + "field_" + strconv.Itoa(i)
								if mode == privacy.ModeFull {
									want = "[REDACTED]"
								} else if i == 0 {
									want = "[REDACTED:marker]"
								}
							}
							if attrs[key] != want {
								t.Errorf("%s: incorrect value or deterministic name for field %d", retention, i)
							}
						}
						if !include {
							if _, ok := attrs["input.value"]; ok {
								t.Error("body capture was enabled by dimension masking")
							}
						}
						c26Metadata(t, attrs, rc)
						if !bytes.Equal(before, c26Snapshot(t, rc)) {
							t.Error("live caller extras changed")
						}
					}
				})
			}
		}
	}
}

func TestC26R2BodyFieldNameCaps(t *testing.T) {
	for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull} {
		t.Run(mode, func(t *testing.T) {
			p, _ := bodyPlugin(t, false)
			t.Cleanup(p.Close)
			rc := newTestRC()
			rc.CallerExtras = map[string]any{}
			for i := 0; i < maxMetadataPairs+2; i++ {
				rc.CallerExtras[fmt.Sprintf("f%02d-", i)+c26Marker()] = 7
			}
			rc.CallerExtras[strings.Repeat("z", maxMetadataKeyLen+1)] = 9
			rc.CallerExtrasDropped = 2
			c26Policy(p, rc, "global", mode)
			attrs := c26Export(t, p, rc)
			if attrs[bodyDroppedAttr] != float64(5) {
				t.Error("pre-existing drop accounting changed")
			}
			for i := 0; i < maxMetadataPairs; i++ {
				if _, ok := attrs[bodyAttrPrefix+"field_"+strconv.Itoa(i)]; !ok {
					t.Errorf("missing categorical field %d", i)
				}
			}
			if bytes.Contains(c26JSON(t, attrs), []byte(c26Marker())) {
				t.Error("a capped body name remained raw")
			}
		})
	}
}

func TestC26R2MetadataValues(t *testing.T) {
	for _, source := range []string{"org", "global"} {
		for _, mode := range []string{privacy.ModePatterns, privacy.ModeFull, privacy.ModeNone, "absent"} {
			for _, sink := range []string{"blob", "flattened"} {
				t.Run(source+"/"+mode+"/"+sink, func(t *testing.T) {
					for _, retention := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
						for _, value := range []string{
							c26Marker(), c26QuotedMarker(),
							`{"value":"` + c26EscapedMarker() + `","count":9007199254740993}`,
							strings.Repeat("x", maxMetadataValueLen-12) + c26Marker(),
						} {
							p, _ := bodyPlugin(t, false)
							t.Cleanup(p.Close)
							p.metadataAttributes = sink == "flattened"
							rc := newTestRC()
							rc.Privacy.ContentMode = retention
							rc.Metadata["tag"] = value
							rc.Metadata["ordinary"] = "group-a"
							rc.CustomMetadataKeys = []string{"tag", "ordinary"}
							c26Policy(p, rc, source, mode)
							before := c26Snapshot(t, rc)
							attrs := c26Export(t, p, rc)
							var blob map[string]string
							raw, ok := attrs["metadata"].(string)
							if !ok {
								t.Fatal("metadata blob missing")
							}
							if err := json.Unmarshal([]byte(raw), &blob); err != nil {
								t.Fatal(err)
							}
							got := blob["tag"]
							if sink == "flattened" {
								flat, ok := attrs[metadataAttrPrefix+"tag"].(string)
								if !ok || flat != got {
									t.Fatal("flattened and JSON metadata diverged")
								}
								got = flat
							} else if _, exists := attrs[metadataAttrPrefix+"tag"]; exists {
								t.Error("disabled flattening emitted metadata attribute")
							}
							if mode == privacy.ModePatterns || mode == privacy.ModeFull {
								c26Masked(t, got)
								if mode == privacy.ModePatterns && strings.Contains(value, "9007199254740993") && !strings.Contains(got, "9007199254740993") {
									t.Error("decoded metadata rounded an unrelated number")
								}
							} else if got != truncateAtRune(value, maxMetadataValueLen) {
								t.Errorf("%s: disabled metadata value changed", retention)
							}
							wantOrdinary := "group-a"
							if mode == privacy.ModeFull {
								wantOrdinary = "[REDACTED]"
							}
							if blob["ordinary"] != wantOrdinary {
								t.Error("benign metadata did not follow the selected mode")
							}
							c26Metadata(t, attrs, rc)
							if !bytes.Equal(before, c26Snapshot(t, rc)) {
								t.Error("caller metadata or resolved policy changed")
							}
						}
					}
				})
			}
		}
	}
}
