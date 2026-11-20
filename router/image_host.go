package router

import (
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/middleware"

	"github.com/gin-gonic/gin"
)

func SetImageHostRouter(router *gin.Engine) {
	g := router.Group("/v1/images/host")
	g.Use(middleware.RouteTag("api"))
	g.POST("", middleware.TokenAuth(), controller.UploadHostedImage)
	g.GET("/:name", controller.GetHostedImage)
}
