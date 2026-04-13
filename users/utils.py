from django.conf import settings
from django.core.mail import EmailMessage

def send_password_reset_email(user_email, subject, message):
    """
    Helper function to send password reset emails with proper headers
    to avoid spam filters
    """
    email = EmailMessage(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user_email],
        headers={
            'List-Unsubscribe': f'<mailto:unsubscribe@{settings.CUSTOM_DOMAIN}>',
            'X-Priority': '1',  # High priority
            'X-MSMail-Priority': 'High',
            'Importance': 'High',
        }
    )
    # Set content type to HTML
    email.content_subtype = "html"
    return email.send(fail_silently=False)