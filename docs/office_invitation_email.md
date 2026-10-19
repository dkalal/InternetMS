# Office invitation email setup

The web container loads `.env` through Docker Compose. Replace the example `APP_BASE_URL` with the office server's address reachable from other office devices, including port 8000. Do not use localhost, a Docker hostname, or the example documentation address. Keep `.env` out of Git.

Enable Google 2-Step Verification on the sending account. If Google offers App Passwords for that account, create one and put it in `EMAIL_HOST_PASSWORD` in the local `.env`; use the full Gmail address for `EMAIL_HOST_USER` and `DEFAULT_FROM_EMAIL`. Set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST=smtp.gmail.com`, `EMAIL_PORT=587`, `EMAIL_USE_TLS=1`, and `EMAIL_USE_SSL=0`. Console backend is for deliberate development use only and does not deliver mail.

After changing only `.env`, recreate the web container with `docker compose up -d --force-recreate web`. After changing application code or pulling code changes, rebuild and recreate it with `docker compose up -d --build web`; this Compose setup does not mount the source tree into the container. Check effective settings without printing credentials:

```sh
docker compose exec web python manage.py shell -c "from django.conf import settings; print(settings.EMAIL_BACKEND, settings.EMAIL_HOST, settings.EMAIL_PORT, settings.APP_BASE_URL)"
```

To send one manual test to an address you control, replace the placeholder recipient and run:

```sh
docker compose exec web python manage.py shell -c "from django.core.mail import send_mail; from django.conf import settings; print(send_mail('JBMS email test', 'Office SMTP test', settings.DEFAULT_FROM_EMAIL, ['recipient@example.com']))"
```

Check Inbox and Spam, then invite a test account and open its link from another office device. SMTP acceptance only means the provider accepted the message. It does not prove Inbox delivery. The invitation page itself uses the configured HTTP or HTTPS address; SMTP TLS does not protect password entry there. Use HTTPS on the office network or a trusted VPN when possible.

## Account setup on a shared browser

Invitations and password pages use a separate account-access layout without workspace actions. If the browser is already signed in as another user, the invitation asks that user to sign out before the invitee can set a password or accept access. This signs out only that browser; a session on a different device is independent. After setting a password, the invitee signs in with their own account. Administrators should sign out when handing a shared computer to another person. The server still checks tenant membership and permissions on each protected action.

The account pages send only the site origin as a referrer, so invitation tokens in URL paths are not sent to other sites and normal form submissions keep a valid `Origin` for CSRF checks. Open the office URL directly in the browser; if an older login page remains open after deployment, reload it before submitting the form.
