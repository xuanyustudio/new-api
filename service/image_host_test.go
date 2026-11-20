package service

import (
	"bytes"
	"encoding/base64"
	"errors"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"
)

func TestSaveHostedImageAndOpen(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("IMAGE_HOST_DIR", dir)

	data := tinyPNG(t)
	name, err := SaveHostedImage(data)
	if err != nil {
		t.Fatal(err)
	}
	path, contentType, err := OpenHostedImage(name)
	if err != nil {
		t.Fatal(err)
	}
	if contentType != "image/png" {
		t.Fatalf("content type %s", contentType)
	}
	if filepath.Dir(path) != dir && filepath.Clean(filepath.Dir(path)) != filepath.Clean(dir) {
		t.Fatalf("stored outside host dir: %s", path)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, data) {
		t.Fatal("stored bytes differ")
	}
}

func TestSaveHostedImageRejectsNonImage(t *testing.T) {
	t.Setenv("IMAGE_HOST_DIR", t.TempDir())
	if _, err := SaveHostedImage([]byte("not an image")); !errors.Is(err, ErrHostedImageType) {
		t.Fatalf("got %v", err)
	}
}

func TestOpenHostedImageRejectsTraversal(t *testing.T) {
	t.Setenv("IMAGE_HOST_DIR", t.TempDir())
	for _, name := range []string{"../secret.png", `..\secret.png`, "not-a-name.png", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg/../x"} {
		if _, _, err := OpenHostedImage(name); !errors.Is(err, ErrHostedImageNotFound) {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestDecodeHostedImagePayload(t *testing.T) {
	data := tinyPNG(t)
	encoded := "data:image/png;base64," + base64.StdEncoding.EncodeToString(data)
	got, err := DecodeHostedImagePayload(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, data) {
		t.Fatal("decoded bytes differ")
	}
	if _, err := DecodeHostedImagePayload("https://example.com/a.png"); !errors.Is(err, ErrHostedImageURL) {
		t.Fatalf("url: %v", err)
	}
}

func tinyPNG(t *testing.T) []byte {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, 1, 1))
	img.Set(0, 0, color.NRGBA{R: 255, A: 255})
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}
