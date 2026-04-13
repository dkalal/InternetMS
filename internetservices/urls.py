"""
URL configuration for internetservices project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# core/urls.py
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
# Add this import for auth_views
from django.contrib.auth import views as auth_views
from users.forms import TailwindAuthenticationForm

# Import views for test_email and test_email_multiple
# from users import views


def _strict_admin_has_permission(self, request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(getattr(user, "is_superuser", False))


AdminSite.has_permission = _strict_admin_has_permission


urlpatterns = [
    path('admin/', admin.site.urls),
    path('customers/', include('customers.urls')),
    path('services/', include('services.urls')),
    path('products/', include('products.urls')),
    path('billing/', include('billing.urls')),
    path('users/', include('users.urls')),  # This includes the register URL
    

    # Authentication URLs
    path('accounts/login/', auth_views.LoginView.as_view(template_name='auth/login.html', authentication_form=TailwindAuthenticationForm), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(template_name='auth/logout.html'), name='logout'),
    # path('logout/', auth_views.LogoutView.as_view(template_name='registration/logged_out.html'), name='logout'),

    # path('', include('accounts.urls')),
    # path('', RedirectView.as_view(url='customers/', permanent=True)),

    # Redirect root URL to login page if needed
    path('', RedirectView.as_view(url='/accounts/login/'), name='home'),
    # In your urls.py
    # path('test-email/', views.test_email, name='test_email'),
    # path('test-email-multiple/', views.test_email_multiple, name='test_email_multiple'),
]

# Add this at the end of the file to serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
