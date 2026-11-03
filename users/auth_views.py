from django.contrib.auth.views import LoginView
from django.urls import reverse


class WorkspaceLoginView(LoginView):
    """Keep Django's validated ``next`` redirect, with a role-aware fallback."""

    def get_default_redirect_url(self):
        return reverse("main_app:workspace_home")
