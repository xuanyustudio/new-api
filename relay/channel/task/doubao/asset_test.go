package doubao

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
)

func TestAssetEndpointStripsAPIVersion(t *testing.T) {
	t.Parallel()
	got := assetEndpoint("https://api-v2.xingchenzhichuang.com/api/v3/", "CreateAsset")
	want := "https://api-v2.xingchenzhichuang.com/api/v3/asset?Action=CreateAsset&Version=2024-01-01"
	if got != want {
		t.Fatalf("got %s", got)
	}
}

func TestVolcAssetAuthEnabled(t *testing.T) {
	t.Parallel()
	if volcAssetAuthEnabled(dto.ChannelOtherSettings{}) {
		t.Fatal("missing switch should be off")
	}
	off := false
	if volcAssetAuthEnabled(dto.ChannelOtherSettings{VolcengineAssetAuth: &off}) {
		t.Fatal("explicit false should be off")
	}
	on := true
	if !volcAssetAuthEnabled(dto.ChannelOtherSettings{VolcengineAssetAuth: &on}) {
		t.Fatal("explicit true should be on")
	}
}

func TestApplyAssetAuthRewritesMedia(t *testing.T) {
	resetAssetGroupCache()
	t.Cleanup(resetAssetGroupCache)

	var groups atomic.Int32
	var creates atomic.Int32
	var gets sync.Map
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v3/asset" {
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			http.Error(w, "bad auth", http.StatusUnauthorized)
			return
		}
		var payload map[string]string
		if err := common.DecodeJson(r.Body, &payload); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Query().Get("Action") {
		case "CreateAssetGroup":
			groups.Add(1)
			_, _ = w.Write([]byte(`{"ResponseMetadata":{"Action":"CreateAssetGroup"},"Result":{"Id":"group-auto"}}`))
		case "CreateAsset":
			creates.Add(1)
			if payload["GroupId"] != "group-auto" {
				http.Error(w, "bad group", http.StatusBadRequest)
				return
			}
			id := "asset-img"
			switch payload["AssetType"] {
			case "Video":
				id = "asset-vid"
			case "Audio":
				id = "asset-aud"
			}
			_, _ = w.Write([]byte(`{"Result":{"Id":"` + id + `"}}`))
		case "GetAsset":
			n := 0
			if v, ok := gets.Load(payload["Id"]); ok {
				n = v.(int)
			}
			n++
			gets.Store(payload["Id"], n)
			status := "Processing"
			if n >= 2 {
				status = "Active"
			}
			_, _ = w.Write([]byte(`{"Result":{"Id":"` + payload["Id"] + `","Status":"` + status + `"}}`))
		default:
			http.Error(w, "unknown action", http.StatusBadRequest)
		}
	}))
	defer server.Close()

	on := true
	adaptor := &TaskAdaptor{}
	adaptor.Init(&relaycommon.RelayInfo{
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelId:      46,
			ChannelBaseUrl: server.URL,
			ApiKey:         "test-key",
			ChannelOtherSettings: dto.ChannelOtherSettings{
				VolcengineAssetAuth: &on,
			},
		},
	})
	adaptor.assetHTTP = server.Client()
	adaptor.assetPollInterval = time.Nanosecond
	adaptor.assetPollTimeout = 2 * time.Second

	body := &requestPayload{
		Model: "doubao-seedance-2-0-260128",
		Content: []ContentItem{
			{Type: "text", Text: "a cat"},
			{Type: "image_url", ImageURL: &MediaURL{URL: "https://cdn.example/a.png"}},
			{Type: "video_url", VideoURL: &MediaURL{URL: "https://cdn.example/b.mp4"}, Role: "reference_video"},
			{Type: "audio_url", AudioURL: &MediaURL{URL: "https://cdn.example/c.mp3"}},
			{Type: "image_url", ImageURL: &MediaURL{URL: "asset://asset-ready"}},
		},
	}
	if err := adaptor.applyAssetAuth(context.Background(), body); err != nil {
		t.Fatalf("apply: %v", err)
	}
	if body.Content[1].ImageURL.URL != "asset://asset-img" {
		t.Fatalf("image %s", body.Content[1].ImageURL.URL)
	}
	if body.Content[2].VideoURL.URL != "asset://asset-vid" || body.Content[2].Role != "reference_video" {
		t.Fatalf("video %+v", body.Content[2])
	}
	if body.Content[3].AudioURL.URL != "asset://asset-aud" {
		t.Fatalf("audio %s", body.Content[3].AudioURL.URL)
	}
	if body.Content[4].ImageURL.URL != "asset://asset-ready" {
		t.Fatalf("existing %s", body.Content[4].ImageURL.URL)
	}
	if groups.Load() != 1 {
		t.Fatalf("groups %d", groups.Load())
	}
	if creates.Load() != 3 {
		t.Fatalf("creates %d", creates.Load())
	}

	body2 := &requestPayload{Content: []ContentItem{{Type: "image_url", ImageURL: &MediaURL{URL: "https://cdn.example/d.png"}}}}
	if err := adaptor.applyAssetAuth(context.Background(), body2); err != nil {
		t.Fatalf("second apply: %v", err)
	}
	if groups.Load() != 1 {
		t.Fatalf("group reused, got %d group creates", groups.Load())
	}
	if body2.Content[0].ImageURL.URL != "asset://asset-img" {
		t.Fatalf("second image %s", body2.Content[0].ImageURL.URL)
	}
}

func TestApplyAssetAuthUsesConfiguredGroup(t *testing.T) {
	resetAssetGroupCache()
	t.Cleanup(resetAssetGroupCache)

	var groups atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]string
		_ = common.DecodeJson(r.Body, &payload)
		switch r.URL.Query().Get("Action") {
		case "CreateAssetGroup":
			groups.Add(1)
			http.Error(w, "should not create", http.StatusBadRequest)
		case "CreateAsset":
			if payload["GroupId"] != "group-fixed" {
				http.Error(w, "bad group", http.StatusBadRequest)
				return
			}
			_, _ = w.Write([]byte(`{"Id":"asset-fixed"}`))
		case "GetAsset":
			_, _ = w.Write([]byte(`{"Status":"Active","Id":"asset-fixed"}`))
		}
	}))
	defer server.Close()

	adaptor := &TaskAdaptor{
		assetAuth:         true,
		assetGroupID:      "group-fixed",
		baseURL:           server.URL,
		apiKey:            "k",
		channelID:         7,
		assetHTTP:         server.Client(),
		assetPollInterval: time.Nanosecond,
		assetPollTimeout:  2 * time.Second,
	}
	body := &requestPayload{Content: []ContentItem{{Type: "image_url", ImageURL: &MediaURL{URL: "http://cdn.example/a.jpg"}}}}
	if err := adaptor.applyAssetAuth(context.Background(), body); err != nil {
		t.Fatalf("apply: %v", err)
	}
	if body.Content[0].ImageURL.URL != "asset://asset-fixed" {
		t.Fatalf("url %s", body.Content[0].ImageURL.URL)
	}
	if groups.Load() != 0 {
		t.Fatal("created group")
	}
}

func TestEnsureAssetRefRejectsDataURL(t *testing.T) {
	t.Parallel()
	client := &assetClient{pollInterval: time.Nanosecond, pollTimeout: time.Second}
	_, err := client.ensureAssetRef(context.Background(), "Image", "data:image/png;base64,aaaa")
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestEnsureAssetRefFailedStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Query().Get("Action") {
		case "CreateAsset":
			_, _ = w.Write([]byte(`{"Result":{"Id":"asset-bad"}}`))
		case "GetAsset":
			_, _ = w.Write([]byte(`{"Result":{"Id":"asset-bad","Status":"Failed","Error":{"Message":"moderation rejected"}}}`))
		}
	}))
	defer server.Close()
	client := &assetClient{
		baseURL:      server.URL,
		groupID:      "group-1",
		httpClient:   server.Client(),
		pollInterval: time.Nanosecond,
		pollTimeout:  time.Second,
	}
	_, err := client.ensureAssetRef(context.Background(), "Image", "https://cdn.example/a.png")
	if err == nil || !strings.Contains(err.Error(), "moderation rejected") {
		t.Fatalf("err %v", err)
	}
}

func TestBuildRequestBodySkipsAssetAuthWhenOff(t *testing.T) {
	gin.SetMode(gin.TestMode)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/video/generations", nil)
	c.Set("task_request", relaycommon.TaskSubmitReq{
		Model:  "doubao-seedance-2-0-260128",
		Prompt: "hello",
		Images: []string{"https://cdn.example/a.png"},
	})
	adaptor := &TaskAdaptor{baseURL: "https://api-v2.xingchenzhichuang.com"}
	reader, err := adaptor.BuildRequestBody(c, &relaycommon.RelayInfo{ChannelMeta: &relaycommon.ChannelMeta{}})
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	var payload requestPayload
	if err := common.DecodeJson(reader, &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	found := false
	for _, item := range payload.Content {
		if item.ImageURL != nil && item.ImageURL.URL == "https://cdn.example/a.png" {
			found = true
		}
	}
	if !found {
		t.Fatalf("image url was rewritten: %+v", payload.Content)
	}
}
