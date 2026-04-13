# import logging
# import socket
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib import messages
from .forms import UserRegisterForm
from django.core.mail import send_mail, EmailMessage
# from django.http import HttpResponse
from django.contrib.auth.decorators import user_passes_test
# from smtplib import SMTPException, SMTPAuthenticationError, SMTPConnectError, SMTPServerDisconnected
# from django.utils import timezone
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.shortcuts import render
from django.conf import settings
from django.contrib.auth import get_user_model
from .utils import send_password_reset_email  # Import our helper function
from .models import Organization, Membership, OrganizationBranding, UserAccessProfile
from django.contrib.auth.decorators import login_required

from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization
from .forms import OrganizationBrandingForm

# Create your views here.

User = get_user_model()

def custom_password_reset(request):
    """Custom password reset implementation for development environment"""
    if request.method == 'POST':
        email = request.POST.get('email', '')
        user = User.objects.filter(email=email).first()
        
        if user:
            # Generate token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Always use the request's host for development
            domain = request.get_host()  # This will be localhost:8000 usually
            protocol = 'http'  # Use http for development
            
            # Explicitly build the reset URL with localhost domain
            reset_url = f"{protocol}://{domain}/reset/{uid}/{token}/"
            
            # Render the email template with the correct URL
            context = {
                'user': user,
                'reset_url': reset_url,
                'protocol': protocol,
                'domain': domain,
                'uid': uid,
                'token': token,
                'site_name': 'JS Internet Services',
            }
            
            email_subject = 'Reset Your JS Internet Services Password'
            email_body = render_to_string('registration/password_reset_email_custom.html', context)
            
            # Send email with proper headers to avoid spam filters
            email = EmailMessage(
                subject=email_subject,
                body=email_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
                headers={
                    'List-Unsubscribe': f'<mailto:unsubscribe@example.com>',
                    'X-Priority': '1',  # High priority
                    'X-MSMail-Priority': 'High',
                    'Importance': 'High',
                }
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=False)
            
            return HttpResponse("Password reset email sent! Please check your inbox and spam folder.<br><br>"
                               f"<strong>Development Mode:</strong> The reset link will be: {reset_url}")
        
        # Always return success even if email not found to prevent user enumeration
        return HttpResponse("If your email is registered, you will receive password reset instructions.")
    
    # Show the form
    return render(request, 'registration/password_reset_form.html')

def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}!')

            org = Organization.objects.filter(slug='default', is_active=True).first()
            if org:
                Membership.objects.get_or_create(
                    organization=org,
                    user=user,
                    defaults={'role': 'member', 'is_active': True},
                )
                UserAccessProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "tenant": org,
                        "role": UserAccessProfile.Role.TENANT_STAFF,
                    },
                )

            # Automatically log in user after registration
            raw_password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=raw_password)
            login(request, user)
            return redirect('customer-list')
    else:
        form = UserRegisterForm()
    return render(request, 'auth/register.html', {'form': form})


@login_required
def branding_settings(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.BILLING_SETTINGS_CHANGE)

    branding, _ = OrganizationBranding.objects.get_or_create(organization=organization)

    if request.method == "POST":
        form = OrganizationBrandingForm(request.POST, request.FILES, instance=branding)
        if form.is_valid():
            form.save()
            messages.success(request, "Branding updated.")
            return redirect("branding_settings")
    else:
        form = OrganizationBrandingForm(instance=branding)

    return render(request, "users/branding_settings.html", {"form": form, "branding": branding, "organization": organization})


# Set up a logger for email-related issues
# logger = logging.getLogger('email_tests')

# @user_passes_test(lambda u: u.is_superuser)  # Only superusers can access this test view
# def test_email(request):
#     sender = 'dullakalal360@gmail.com'
#     recipient = 'kilionetrekkingandsafari@gmail.com'
#     test_results = []
    
#     # Test 1: Check DNS resolution for SMTP server
#     try:
#         socket.gethostbyname('smtp.gmail.com')
#         test_results.append("✅ DNS resolution for smtp.gmail.com successful")
#     except socket.gaierror:
#         test_results.append("❌ DNS resolution for smtp.gmail.com failed - network or DNS issue")
    
#     # Test 2: Check if port 587 is reachable
#     try:
#         sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         sock.settimeout(5)
#         result = sock.connect_ex(('smtp.gmail.com', 587))
#         if result == 0:
#             test_results.append("✅ Connection to smtp.gmail.com:587 successful")
#         else:
#             test_results.append(f"❌ Connection to smtp.gmail.com:587 failed with error code {result}")
#         sock.close()
#     except Exception as e:
#         test_results.append(f"❌ Socket connection error: {str(e)}")
    
#     # Test 3: Try sending email
#     try:
#         send_mail(
#             'Test Email from JS Internet Services',
#             'This is a test email to verify the email configuration is working properly. Sent at: ' + 
#             str(timezone.now()),
#             sender,
#             [recipient],
#             fail_silently=False,
#         )
#         test_results.append(f"✅ Email sent successfully to {recipient}")
#         logger.info(f"Test email sent successfully to {recipient}")
#     except SMTPAuthenticationError as e:
#         error_message = f"❌ SMTP Authentication Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPConnectError as e:
#         error_message = f"❌ SMTP Connection Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPServerDisconnected as e:
#         error_message = f"❌ SMTP Server Disconnected: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPException as e:
#         error_message = f"❌ SMTP Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except Exception as e:
#         error_message = f"❌ Unexpected Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
    
#     # Add email configuration details to the response (with password partially masked)
#     from django.conf import settings
#     email_config = {
#         'EMAIL_BACKEND': settings.EMAIL_BACKEND,
#         'EMAIL_HOST': settings.EMAIL_HOST,
#         'EMAIL_PORT': settings.EMAIL_PORT,
#         'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
#         'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
#         'EMAIL_HOST_PASSWORD': '****' + settings.EMAIL_HOST_PASSWORD[-4:] if settings.EMAIL_HOST_PASSWORD else 'Not set',
#         'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
#     }
    
#     config_details = "<h3>Current Email Configuration:</h3>"
#     for key, value in email_config.items():
#         config_details += f"<p><strong>{key}:</strong> {value}</p>"
    
#     # Format results into a nice HTML response
#     results_html = "<h3>Email System Tests:</h3><ul>"
#     for result in test_results:
#         results_html += f"<li>{result}</li>"
#     results_html += "</ul>"
    
#     return HttpResponse(
#         f"<h1>Email System Test Results</h1>{results_html}{config_details}"
#         f"<p>If email was sent successfully but not received, please check:</p>"
#         f"<ul>"
#         f"<li>Spam/junk folder in {recipient}</li>"
#         f"<li>Gmail sending limits (especially for new accounts)</li>"
#         f"<li>Gmail's App Password settings</li>"
#         f"<li>Gmail's 'Less secure app access' settings (if applicable)</li>"
#         f"</ul>"
#     )

# # You can also add a view to check email delivery more thoroughly
# @user_passes_test(lambda u: u.is_superuser)
# def test_email_multiple(request):
#     """Test email delivery to multiple providers to identify if issue is provider-specific"""
#     test_emails = [
#         'kilionetrekkingandsafari@gmail.com',  # Gmail
#         # Add your other test emails here (e.g., Outlook, Yahoo, etc.)
#     ]
    
#     results = []
#     for recipient in test_emails:
#         try:
#             send_mail(
#                 f'This is a test email sent to {recipient} at {timezone.now()}',
#                 f'This is a test email sent to {recipient} at {str(timezone.now())}',
#                 'dullakalal360@gmail.com',
#                 [recipient],
#                 fail_silently=False,
#             )
#             results.append(f"✅ Email to {recipient}: SENT")
#         except Exception as e:
#             results.append(f"❌ Email to {recipient}: FAILED - {str(e)}")
    
#     results_html = "<h3>Multiple Provider Test Results:</h3><ul>"
#     for result in results:
#         results_html += f"<li>{result}</li>"
#     results_html += "</ul>"
    
#     return HttpResponse(f"<h1>Multiple Email Provider Test</h1>{results_html}")
