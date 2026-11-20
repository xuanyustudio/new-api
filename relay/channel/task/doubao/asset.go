package doubao

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/tidwall/gjson"
)

const (
	assetAPIVersion   = "2024-01-01"
	assetProjectName  = "default"
	assetPollInterval = 2 * time.Second
	assetPollTimeout  = 120 * time.Second
	assetErrorBodyMax = 300
)

func volcAssetAuthEnabled(settings dto.ChannelOtherSettings) bool {
	return settings.VolcengineAssetAuth != nil && *settings.VolcengineAssetAuth
}

func requestContext(c *gin.Context) context.Context {
	if c != nil && c.Request != nil {
		return c.Request.Context()
	}
	return context.Background()
}

type assetClient struct {
	baseURL      string
	apiKey       string
	channelID    int
	groupID      string
	httpClient   *http.Client
	pollInterval time.Duration
	pollTimeout  time.Duration
}

var (
	assetGroupMu    sync.Mutex
	assetGroupCache = map[string]string{}
	assetGroupLocks = map[string]*sync.Mutex{}
)

func resetAssetGroupCache() {
	assetGroupMu.Lock()
	assetGroupCache = map[string]string{}
	assetGroupLocks = map[string]*sync.Mutex{}
	assetGroupMu.Unlock()
}

func assetGroupLock(key string) *sync.Mutex {
	assetGroupMu.Lock()
	lock, ok := assetGroupLocks[key]
	if !ok {
		lock = &sync.Mutex{}
		assetGroupLocks[key] = lock
	}
	assetGroupMu.Unlock()
	return lock
}

func (a *TaskAdaptor) applyAssetAuth(ctx context.Context, body *requestPayload) error {
	if a == nil || !a.assetAuth || body == nil || len(body.Content) == 0 {
		return nil
	}
	client, err := a.newAssetClient()
	if err != nil {
		return err
	}
	indexes := make([]int, 0)
	for i := range body.Content {
		_, raw := contentMedia(&body.Content[i])
		if raw == "" || isAssetRef(raw) {
			if isAssetRef(raw) {
				setMediaRef(&body.Content[i], normalizeAssetRef(raw))
			}
			continue
		}
		indexes = append(indexes, i)
	}
	if len(indexes) == 0 {
		return nil
	}

	refs := make([]string, len(indexes))
	errs := make([]error, len(indexes))
	var wg sync.WaitGroup
	wg.Add(len(indexes))
	for n, idx := range indexes {
		n, idx := n, idx
		go func() {
			defer wg.Done()
			assetType, raw := contentMedia(&body.Content[idx])
			ref, err := client.ensureAssetRef(ctx, assetType, raw)
			refs[n] = ref
			errs[n] = err
		}()
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			return err
		}
	}
	for n, idx := range indexes {
		setMediaRef(&body.Content[idx], refs[n])
	}
	return nil
}

func (a *TaskAdaptor) newAssetClient() (*assetClient, error) {
	httpClient := a.assetHTTP
	if httpClient == nil {
		var err error
		httpClient, err = service.GetHttpClientWithProxy(a.proxy)
		if err != nil {
			return nil, err
		}
		if httpClient == nil {
			httpClient = http.DefaultClient
		}
		httpClient = withAssetTimeout(httpClient)
	}
	interval := a.assetPollInterval
	timeout := a.assetPollTimeout
	if interval <= 0 {
		interval = assetPollInterval
	}
	if timeout <= 0 {
		timeout = assetPollTimeout
	}
	return &assetClient{
		baseURL:      a.baseURL,
		apiKey:       a.apiKey,
		channelID:    a.channelID,
		groupID:      a.assetGroupID,
		httpClient:   httpClient,
		pollInterval: interval,
		pollTimeout:  timeout,
	}, nil
}

func withAssetTimeout(base *http.Client) *http.Client {
	want := assetPollTimeout + 30*time.Second
	if base == nil {
		return &http.Client{Timeout: want}
	}
	if base.Timeout == 0 || base.Timeout >= want {
		return base
	}
	clone := *base
	clone.Timeout = want
	return &clone
}

func (c *assetClient) cacheKey() string {
	return fmt.Sprintf("%d|%s", c.channelID, normalizeBaseURL(c.baseURL))
}

func (c *assetClient) ensureGroup(ctx context.Context) (string, error) {
	if id := strings.TrimSpace(c.groupID); id != "" {
		return id, nil
	}
	key := c.cacheKey()
	assetGroupMu.Lock()
	if id, ok := assetGroupCache[key]; ok && id != "" {
		assetGroupMu.Unlock()
		return id, nil
	}
	assetGroupMu.Unlock()

	lock := assetGroupLock(key)
	lock.Lock()
	defer lock.Unlock()

	assetGroupMu.Lock()
	if id, ok := assetGroupCache[key]; ok && id != "" {
		assetGroupMu.Unlock()
		return id, nil
	}
	assetGroupMu.Unlock()

	id, err := c.createGroup(ctx)
	if err != nil {
		return "", err
	}
	assetGroupMu.Lock()
	assetGroupCache[key] = id
	assetGroupMu.Unlock()
	return id, nil
}

func (c *assetClient) createGroup(ctx context.Context) (string, error) {
	name := "new-api-auto"
	if c.channelID > 0 {
		name = fmt.Sprintf("new-api-%d", c.channelID)
	}
	result, err := c.call(ctx, "CreateAssetGroup", map[string]string{
		"Name":        name,
		"Description": "auto asset group for video",
		"GroupType":   "AIGC",
		"ProjectName": assetProjectName,
	})
	if err != nil {
		return "", err
	}
	id := assetResultID(result)
	if id == "" {
		return "", fmt.Errorf("CreateAssetGroup did not return an id")
	}
	return id, nil
}

func (c *assetClient) ensureAssetRef(ctx context.Context, assetType, raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", fmt.Errorf("empty media url")
	}
	if isAssetRef(raw) {
		return normalizeAssetRef(raw), nil
	}
	if !isHTTPURL(raw) {
		return "", fmt.Errorf("unsupported media reference %q; use an http(s) URL or asset://", raw)
	}
	groupID, err := c.ensureGroup(ctx)
	if err != nil {
		return "", err
	}
	result, err := c.call(ctx, "CreateAsset", map[string]string{
		"GroupId":     groupID,
		"URL":         raw,
		"AssetType":   assetType,
		"ProjectName": assetProjectName,
	})
	if err != nil {
		return "", err
	}
	id := assetResultID(result)
	if id == "" {
		return "", fmt.Errorf("CreateAsset did not return an id")
	}
	if err := c.waitActive(ctx, id); err != nil {
		return "", err
	}
	return "asset://" + id, nil
}

func (c *assetClient) waitActive(ctx context.Context, id string) error {
	timeout := c.pollTimeout
	if timeout <= 0 {
		timeout = assetPollTimeout
	}
	interval := c.pollInterval
	if interval <= 0 {
		interval = assetPollInterval
	}
	deadline := time.Now().Add(timeout)
	for {
		result, err := c.call(ctx, "GetAsset", map[string]string{
			"Id":          id,
			"ProjectName": assetProjectName,
		})
		if err != nil {
			return err
		}
		status := strings.ToLower(strings.TrimSpace(result.Get("Status").String()))
		if status == "" {
			status = strings.ToLower(strings.TrimSpace(result.Get("status").String()))
		}
		switch status {
		case "active":
			return nil
		case "failed":
			msg := firstNonEmpty(
				result.Get("Error.Message").String(),
				result.Get("Message").String(),
				result.Get("message").String(),
			)
			if msg == "" {
				msg = "processing failed"
			}
			return fmt.Errorf("asset %s failed: %s", id, msg)
		}
		if !time.Now().Before(deadline) {
			shown := status
			if shown == "" {
				shown = "unknown"
			}
			return fmt.Errorf("asset %s not active within %s (status %s)", id, timeout, shown)
		}
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (c *assetClient) call(ctx context.Context, action string, payload any) (gjson.Result, error) {
	data, err := common.Marshal(payload)
	if err != nil {
		return gjson.Result{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, assetEndpoint(c.baseURL, action), bytes.NewReader(data))
	if err != nil {
		return gjson.Result{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	if strings.TrimSpace(c.apiKey) != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return gjson.Result{}, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return gjson.Result{}, err
	}
	if resp.StatusCode >= 400 {
		return gjson.Result{}, fmt.Errorf("asset %s http %d: %s", action, resp.StatusCode, trimBody(body))
	}
	return parseAssetResult(body)
}

func assetEndpoint(baseURL, action string) string {
	raw := normalizeBaseURL(baseURL)
	if strings.HasSuffix(strings.ToLower(raw), "/api/v3") {
		raw = raw[:len(raw)-len("/api/v3")]
		raw = strings.TrimSuffix(raw, "/")
	}
	return raw + "/api/v3/asset?Action=" + action + "&Version=" + assetAPIVersion
}

func parseAssetResult(body []byte) (gjson.Result, error) {
	raw := strings.TrimSpace(string(body))
	if raw == "" {
		return gjson.Result{}, fmt.Errorf("empty asset response")
	}
	if errMsg := strings.TrimSpace(gjson.Get(raw, "ResponseMetadata.Error.Message").String()); errMsg != "" {
		return gjson.Result{}, fmt.Errorf("%s", errMsg)
	}
	if result := gjson.Get(raw, "Result"); result.Exists() {
		return result, nil
	}
	return gjson.Parse(raw), nil
}

func assetResultID(result gjson.Result) string {
	id := strings.TrimSpace(result.Get("Id").String())
	if id == "" {
		id = strings.TrimSpace(result.Get("id").String())
	}
	return id
}

func contentMedia(item *ContentItem) (assetType, raw string) {
	if item == nil {
		return "", ""
	}
	switch item.Type {
	case "image_url":
		return "Image", mediaURL(item.ImageURL)
	case "video_url":
		return "Video", mediaURL(item.VideoURL)
	case "audio_url":
		return "Audio", mediaURL(item.AudioURL)
	}
	if url := mediaURL(item.ImageURL); url != "" {
		return "Image", url
	}
	if url := mediaURL(item.VideoURL); url != "" {
		return "Video", url
	}
	if url := mediaURL(item.AudioURL); url != "" {
		return "Audio", url
	}
	return "", ""
}

func mediaURL(media *MediaURL) string {
	if media == nil {
		return ""
	}
	return strings.TrimSpace(media.URL)
}

func setMediaRef(item *ContentItem, ref string) {
	if item == nil {
		return
	}
	switch item.Type {
	case "video_url":
		if item.VideoURL == nil {
			item.VideoURL = &MediaURL{}
		}
		item.VideoURL.URL = ref
	case "audio_url":
		if item.AudioURL == nil {
			item.AudioURL = &MediaURL{}
		}
		item.AudioURL.URL = ref
	default:
		if item.Type == "image_url" || item.ImageURL != nil || (item.VideoURL == nil && item.AudioURL == nil) {
			if item.ImageURL == nil {
				item.ImageURL = &MediaURL{}
			}
			item.ImageURL.URL = ref
			return
		}
		if item.VideoURL != nil {
			item.VideoURL.URL = ref
			return
		}
		if item.AudioURL == nil {
			item.AudioURL = &MediaURL{}
		}
		item.AudioURL.URL = ref
	}
}

func isAssetRef(raw string) bool {
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(raw)), "asset://")
}

func normalizeAssetRef(raw string) string {
	s := strings.TrimSpace(raw)
	lower := strings.ToLower(s)
	if !strings.HasPrefix(lower, "asset://") {
		return s
	}
	id := strings.TrimPrefix(s[len("asset://"):], "/")
	return "asset://" + strings.TrimSpace(id)
}

func isHTTPURL(raw string) bool {
	lower := strings.ToLower(strings.TrimSpace(raw))
	return strings.HasPrefix(lower, "https://") || strings.HasPrefix(lower, "http://")
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func trimBody(body []byte) string {
	s := strings.TrimSpace(string(body))
	if len(s) > assetErrorBodyMax {
		return s[:assetErrorBodyMax]
	}
	return s
}
