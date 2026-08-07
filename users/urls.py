from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

from .forms import TailwindPasswordResetForm

urlpatterns = [
    path('register/', views.register, name='register'),
    path("settings/branding/", views.branding_settings, name="branding_settings"),
    path("team/", views.team_access, name="team_access"),
    path("team/invite/", views.invite_member, name="invite_member"),
    path("team/<int:membership_id>/resend-activation/", views.resend_member_activation, name="resend_member_activation"),
    path("team/<int:membership_id>/access/", views.update_member_access, name="update_member_access"),
    path("team/<int:membership_id>/status/", views.change_member_status, name="change_member_status"),
    path("support/start/", views.start_support_access, name="start_support_access"),
    path("support/exit/", views.exit_support_access, name="exit_support_access"),
    # path('test-email/', views.test_email, name='test_email'),
    # path('test-email-multiple/', views.test_email_multiple, name='test_email_multiple'),
    path(
        'password_reset/',
        auth_views.PasswordResetView.as_view(
            template_name='auth/password_reset_form.html',
            form_class=TailwindPasswordResetForm,
            email_template_name='registration/password_reset_email.html',
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='password_reset',
    ),
    path(
        'password_reset/done/',
        auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'),
        name='password_reset_done',
    ),
    
    # Standard password reset confirmation views
    path('reset/<uidb64>/<token>/', 
         auth_views.PasswordResetConfirmView.as_view(
             template_name='registration/password_reset_confirm.html',
             success_url=reverse_lazy('password_reset_complete'),
         ), 
         name='password_reset_confirm'),
         
    path('reset/done/', 
         auth_views.PasswordResetCompleteView.as_view(
             template_name='registration/password_reset_complete.html'
         ), 
         name='password_reset_complete'),
]
