package controller

import (
	"errors"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/system_setting"

	"github.com/gin-gonic/gin"
)

var errHostedImageRequest = errors.New("bad image request")

type hostedImageRequest struct {
	Image string `json:"image"`
}

// UploadHostedImage stores an uploaded image on disk and returns a public URL.
func UploadHostedImage(c *gin.Context) {
	data, err := readUploadedImage(c)
	if err != nil {
		writeHostedImageError(c, err)
		return
	}
	name, err := service.SaveHostedImage(data)
	if err != nil {
		writeHostedImageError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"url": hostedImageURL(c, name),
	})
}

// GetHostedImage serves a previously stored image without authentication.
func GetHostedImage(c *gin.Context) {
	path, contentType, err := service.OpenHostedImage(c.Param("name"))
	if err != nil {
		writeHostedImageError(c, err)
		return
	}
	c.Header("Content-Type", contentType)
	c.Header("X-Content-Type-Options", "nosniff")
	c.Header("Cache-Control", "public, max-age=31536000, immutable")
	c.File(path)
}

func readUploadedImage(c *gin.Context) ([]byte, error) {
	contentType := c.ContentType()
	if strings.HasPrefix(contentType, "multipart/form-data") {
		file, err := c.FormFile("file")
		if err != nil {
			if errors.Is(err, http.ErrMissingFile) {
				return nil, errHostedImageRequest
			}
			return nil, err
		}
		if file.Size > service.ImageHostMaxBytes {
			return nil, service.ErrHostedImageTooLarge
		}
		opened, err := file.Open()
		if err != nil {
			return nil, err
		}
		defer opened.Close()
		return service.ReadLimited(opened, service.ImageHostMaxBytes)
	}

	body, err := service.ReadLimited(c.Request.Body, service.ImageHostMaxBytes*2)
	if err != nil {
		return nil, err
	}
	var req hostedImageRequest
	if err := common.Unmarshal(body, &req); err != nil {
		return nil, errHostedImageRequest
	}
	return service.DecodeHostedImagePayload(req.Image)
}

func hostedImageURL(c *gin.Context, name string) string {
	base := strings.TrimRight(strings.TrimSpace(system_setting.ServerAddress), "/")
	if base == "" {
		proto := c.GetHeader("X-Forwarded-Proto")
		if proto != "http" && proto != "https" {
			if c.Request.TLS != nil {
				proto = "https"
			} else {
				proto = "http"
			}
		}
		base = proto + "://" + c.Request.Host
	}
	return base + "/v1/images/host/" + name
}

func writeHostedImageError(c *gin.Context, err error) {
	status := http.StatusBadRequest
	msg := "请用表单字段 file，或 JSON 字段 image"
	switch {
	case errors.Is(err, service.ErrHostedImageEmpty):
		msg = "图片为空"
	case errors.Is(err, service.ErrHostedImageTooLarge):
		msg = "图片不能超过 10MB"
		status = http.StatusRequestEntityTooLarge
	case errors.Is(err, service.ErrHostedImageType):
		msg = "只支持 jpg、png、webp、gif"
	case errors.Is(err, service.ErrHostedImageURL):
		msg = "请提交图片内容，不要提交网址"
	case errors.Is(err, service.ErrHostedImageNotFound):
		msg = "图片不存在"
		status = http.StatusNotFound
	case errors.Is(err, errHostedImageRequest):
	default:
		status = http.StatusInternalServerError
		msg = "图片无法保存"
	}
	c.JSON(status, gin.H{
		"error": gin.H{"message": msg},
	})
}
