package controller

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/QuantumNous/new-api/setting/system_setting"

	"github.com/gin-gonic/gin"
)

func TestUploadAndGetHostedImage(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("IMAGE_HOST_DIR", t.TempDir())
	old := system_setting.ServerAddress
	system_setting.ServerAddress = "http://39.106.85.47:3018"
	t.Cleanup(func() { system_setting.ServerAddress = old })

	body, err := commonMarshal(map[string]string{
		"image": "data:image/png;base64," + base64.StdEncoding.EncodeToString(tinyPNG(t)),
	})
	if err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/host", bytes.NewReader(body))
	c.Request.Header.Set("Content-Type", "application/json")
	UploadHostedImage(c)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d body %s", rec.Code, rec.Body.String())
	}
	var uploaded struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &uploaded); err != nil {
		t.Fatal(err)
	}
	const prefix = "http://39.106.85.47:3018/v1/images/host/"
	if len(uploaded.URL) <= len(prefix) || uploaded.URL[:len(prefix)] != prefix {
		t.Fatalf("url %s", uploaded.URL)
	}
	name := uploaded.URL[len(prefix):]

	getRec := httptest.NewRecorder()
	getCtx, _ := gin.CreateTestContext(getRec)
	getCtx.Request = httptest.NewRequest(http.MethodGet, "/v1/images/host/"+name, nil)
	getCtx.Params = gin.Params{{Key: "name", Value: name}}
	GetHostedImage(getCtx)
	if getRec.Code != http.StatusOK {
		t.Fatalf("get status %d", getRec.Code)
	}
	if ct := getRec.Header().Get("Content-Type"); ct != "image/png" {
		t.Fatalf("content type %s", ct)
	}
}

func TestUploadHostedImageMultipart(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("IMAGE_HOST_DIR", t.TempDir())
	system_setting.ServerAddress = "http://example.test"

	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	part, err := w.CreateFormFile("file", "dot.png")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write(tinyPNG(t)); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/host", &buf)
	c.Request.Header.Set("Content-Type", w.FormDataContentType())
	UploadHostedImage(c)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d body %s", rec.Code, rec.Body.String())
	}
}

func TestUploadHostedImageRejectsURL(t *testing.T) {
	gin.SetMode(gin.TestMode)
	body := []byte(`{"image":"https://example.com/a.png"}`)
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest(http.MethodPost, "/v1/images/host", bytes.NewReader(body))
	c.Request.Header.Set("Content-Type", "application/json")
	UploadHostedImage(c)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status %d body %s", rec.Code, rec.Body.String())
	}
}

func tinyPNG(t *testing.T) []byte {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, 1, 1))
	img.Set(0, 0, color.NRGBA{R: 1, A: 255})
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func commonMarshal(v any) ([]byte, error) {
	return json.Marshal(v)
}
