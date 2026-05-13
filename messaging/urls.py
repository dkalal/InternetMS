from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("templates/", views.template_options, name="template_options"),
    path("preview/", views.preview_message, name="preview_message"),
    path("send/manual/", views.send_manual_message, name="send_manual_message"),
]
