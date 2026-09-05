# LAN application isolation

JS Internet Services and AssetMS run on the same Docker host. Ports isolate
HTTP routing, but cookies and Chromium page zoom are host-scoped and therefore
must not rely on ports as the isolation boundary.

## Canonical addresses

- JS Internet Services: `http://internet.home.arpa:8000`
- AssetMS: `http://assetms.home.arpa:8001`

Keep `http://10.10.10.254:8000` and `:8001` available during migration. The
applications use distinct session and CSRF cookie names, so authentication is
safe on both old and new addresses. Separate hostnames also give Chromium a
separate saved zoom level for each application.

## DNS

Create these records in the office DNS server/router:

```text
internet.home.arpa  A  10.10.10.254
assetms.home.arpa   A  10.10.10.254
```

For a single Windows workstation used for testing, run an elevated editor and
add the equivalent line to `C:\Windows\System32\drivers\etc\hosts`:

```text
10.10.10.254 internet.home.arpa assetms.home.arpa
```

DNS is preferred because it applies consistently to every LAN client. The
reserved `home.arpa` namespace avoids public DNS and the multicast-DNS behavior
associated with `.local`.

## Deployment and verification

Recreate each web container after changing its settings:

```powershell
docker compose up -d --build web
docker compose ps
```

Verify ownership and application identity:

```powershell
docker port js-internetservices-web
docker port assetms_web
curl.exe -I http://internet.home.arpa:8000/health/
curl.exe -I http://assetms.home.arpa:8001/health/
```

After the first deployment, users must sign in once because the old generic
`sessionid` cookie is intentionally no longer used. Reset the old IP-host zoom
to 100% if desired; future zoom changes on the canonical hostnames are
independent.
