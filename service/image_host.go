package service

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/QuantumNous/new-api/common"

	"golang.org/x/image/webp"
)

const ImageHostMaxBytes = 10 << 20

var (
	ErrHostedImageEmpty    = errors.New("empty image")
	ErrHostedImageTooLarge = errors.New("image exceeds 10MB")
	ErrHostedImageType     = errors.New("unsupported image type")
	ErrHostedImageURL      = errors.New("remote url is not accepted")
	ErrHostedImageNotFound = errors.New("image not found")
)

var hostedImageNameRe = regexp.MustCompile(`^[a-f0-9]{32}\.(jpg|png|gif|webp)$`)

// ImageHostDir is the on-disk directory for uploaded images.
// IMAGE_HOST_DIR overrides the default data/images directory, which is relative to the process working directory.
func ImageHostDir() string {
	dir := strings.TrimSpace(common.GetEnvOrDefaultString("IMAGE_HOST_DIR", ""))
	if dir == "" {
		return filepath.Join("data", "images")
	}
	return dir
}

// SaveHostedImage stores image bytes and returns the public file name.
func SaveHostedImage(data []byte) (string, error) {
	if len(data) == 0 {
		return "", ErrHostedImageEmpty
	}
	if len(data) > ImageHostMaxBytes {
		return "", ErrHostedImageTooLarge
	}
	ext, _, err := sniffHostedImage(data)
	if err != nil {
		return "", err
	}
	dir := ImageHostDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", fmt.Errorf("create image dir: %w", err)
	}
	name, err := newHostedImageName(ext)
	if err != nil {
		return "", err
	}
	finalPath, err := hostedImagePath(name)
	if err != nil {
		return "", err
	}
	tmp, err := os.CreateTemp(dir, "up-*")
	if err != nil {
		return "", fmt.Errorf("create temp image: %w", err)
	}
	tmpName := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpName)
		}
	}()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return "", fmt.Errorf("write image: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return "", fmt.Errorf("close image: %w", err)
	}
	if err := os.Rename(tmpName, finalPath); err != nil {
		return "", fmt.Errorf("store image: %w", err)
	}
	cleanup = false
	return name, nil
}

// OpenHostedImage returns the absolute path and content type for a stored file name.
func OpenHostedImage(name string) (string, string, error) {
	path, err := hostedImagePath(name)
	if err != nil {
		return "", "", ErrHostedImageNotFound
	}
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return "", "", ErrHostedImageNotFound
	}
	return path, hostedImageContentType(filepath.Ext(name)), nil
}

// DecodeHostedImagePayload accepts raw base64 or a data:image URL.
func DecodeHostedImagePayload(raw string) ([]byte, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, ErrHostedImageEmpty
	}
	lower := strings.ToLower(raw)
	if strings.HasPrefix(lower, "http://") || strings.HasPrefix(lower, "https://") {
		return nil, ErrHostedImageURL
	}
	if strings.HasPrefix(lower, "data:") {
		comma := strings.Index(raw, ",")
		if comma < 0 {
			return nil, ErrHostedImageType
		}
		raw = raw[comma+1:]
	}
	raw = strings.Map(func(r rune) rune {
		switch r {
		case '\n', '\r', ' ', '\t':
			return -1
		default:
			return r
		}
	}, raw)
	data, err := decodeBase64(raw)
	if err != nil || len(data) == 0 {
		return nil, ErrHostedImageType
	}
	return data, nil
}

func ReadLimited(r io.Reader, max int) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(r, int64(max)+1))
	if err != nil {
		return nil, err
	}
	if len(data) > max {
		return nil, ErrHostedImageTooLarge
	}
	return data, nil
}

func sniffHostedImage(data []byte) (ext, contentType string, err error) {
	if cfg, format, decErr := image.DecodeConfig(bytes.NewReader(data)); decErr == nil && cfg.Width > 0 && cfg.Height > 0 {
		switch format {
		case "jpeg":
			return ".jpg", "image/jpeg", nil
		case "png":
			return ".png", "image/png", nil
		case "gif":
			return ".gif", "image/gif", nil
		}
	}
	if cfg, decErr := webp.DecodeConfig(bytes.NewReader(data)); decErr == nil && cfg.Width > 0 && cfg.Height > 0 {
		return ".webp", "image/webp", nil
	}
	return "", "", ErrHostedImageType
}

func hostedImageContentType(ext string) string {
	switch strings.ToLower(ext) {
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".png":
		return "image/png"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
	default:
		return "application/octet-stream"
	}
}

func newHostedImageName(ext string) (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("name image: %w", err)
	}
	return hex.EncodeToString(buf) + ext, nil
}

func hostedImagePath(name string) (string, error) {
	if !hostedImageNameRe.MatchString(name) {
		return "", ErrHostedImageNotFound
	}
	dir, err := filepath.Abs(ImageHostDir())
	if err != nil {
		return "", err
	}
	full := filepath.Join(dir, name)
	rel, err := filepath.Rel(dir, full)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", ErrHostedImageNotFound
	}
	return full, nil
}

func decodeBase64(raw string) ([]byte, error) {
	if data, err := base64.StdEncoding.DecodeString(raw); err == nil {
		return data, nil
	}
	return base64.RawStdEncoding.DecodeString(raw)
}
