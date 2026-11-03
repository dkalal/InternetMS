# JS Internet Services Compatibility and Platform Audit

## Executive assessment

The exported VS Code Problems list contained 13 CSS compatibility diagnostics in `inventory/static/inventory/css/jims-ui.css`: four high-severity diagnostics for unprefixed filter or mask declarations and nine warnings for scrollbar properties with incomplete support in the browser matrix used by Microsoft Edge Tools. None was a Django exception, database error, tenant-isolation failure, or Tailwind compilation failure.

The implementation now resolves every exported diagnostic in source code. WebKit-prefixed filter and mask declarations precede their standards-based equivalents. Optional scrollbar styling was removed in favor of native, accessible platform scrollbars, while `overflow-y: scroll` preserves a stable classic-scrollbar gutter in bounded regions without relying on `scrollbar-gutter`. No linter rule was disabled and no browser-warning configuration was weakened.

During system verification, the complete Django suite exposed a separate product-image lifecycle defect. Saving a product with an already-committed image name caused the size validator to query storage, even though no new upload was being validated. This made ordinary model saves depend on file availability and caused a `FileNotFoundError` for a valid database reference whose backing test file was intentionally absent. The validator now measures only new, uncommitted uploads.

## Evidence and browser-compatibility decision

The standards properties are not inherently invalid. MDN classifies `backdrop-filter` and `scrollbar-gutter` as Baseline 2024 and `mask-image` as widely available since December 2023, while also warning that older browsers may lack support.[^1] [^2] [^3] The Problems panel therefore reported compatibility risk against an older browser matrix, not malformed CSS.

For `backdrop-filter` and `mask-image`, retaining the unprefixed declarations is important because they are the standards path. Adding the corresponding `-webkit-` declarations first provides the requested Safari and Chromium-family compatibility while allowing the later standard declaration to win where both forms are understood. Both affected surfaces already have opaque or translucent background fallbacks, so content remains readable even if neither filter is available.

The scrollbar decision is intentionally conservative. Custom thin scrollbars are cosmetic, and platform-native scrollbars are more predictable for keyboard, touch, high-contrast, and motor-access needs. MDN explicitly advises caution with thin or hidden scrollbars because they can make scrolling difficult where no alternative input mechanism exists.[^4] WCAG 2.2 requires functionality to remain keyboard operable and recognizes data tables as legitimate two-dimensional scroll regions.[^5] The application therefore keeps explicit scroll regions and visible native controls instead of suppressing compatibility warnings with tooling configuration.

## Current system state

The application is a Django 5.2 modular monolith covering customers, service packages, product catalog, POS/inventory, billing, integrations, messaging, tenant administration, audit records, and technician work reports. The shared-table tenant boundary is presently enforced at the application layer through `TenantMembership`, `ActiveOrganizationMiddleware`, permission codes, tenant-aware managers, service-layer checks, and negative-path tests.

This design has several strong controls already in place:

- Request tenant context is derived from active server-side membership or an explicit audited support session, rather than accepted directly from a tenant identifier in request data.
- Financial and inventory transitions use service methods, atomic transactions, row locks, immutable movement/history records, and tenant-specific numbering.
- RBAC permissions are centralized as stable codes; protected views and services fail closed, while template visibility is treated only as presentation.
- Production settings enable HTTPS redirect, secure cookies, HSTS, content-type sniffing protection, frame denial, API authentication, throttling, and non-root container execution.
- The test suite contains horizontal tenant-isolation and vertical privilege-denial cases across customer, admin, inventory, finance, integration, and work-report paths.

These controls align with OWASP guidance to establish verified tenant context early, scope resource lookups by tenant, deny cross-tenant object access, and continuously test the authorization matrix.[^6] They are nevertheless application controls, not an independent database security boundary. OWASP recommends database-enforced isolation such as PostgreSQL row-level security when the threat model requires defense in depth; PostgreSQL defaults RLS-protected tables to deny when no applicable policy exists, but table owners, superusers, and `BYPASSRLS` roles require special care.[^6] [^7] Introducing RLS safely would require a classified table inventory, a non-bypassing request role, transaction-local tenant context, connection-pool reuse tests, and a dedicated production migration program. It should not be mixed into a browser-compatibility patch.

## Product-image root cause and correction

Django represents an `ImageField` value as a `FieldFile` that delegates file operations to the configured storage backend. Django also notes that an uploaded file is saved as part of model saving and that storage implementations may be local or remote.[^8] Calling `.size` on an already-committed field therefore performs storage I/O; it is not equivalent to measuring an in-memory request upload.

The previous validator ran `image.size` every time `Product.full_clean()` executed. This created three risks:

1. Updating a non-image field could fail because object storage was temporarily unavailable.
2. Legacy or externally migrated database references could not be saved unless the object was locally present.
3. Remote storage size calls would add latency and cost to unrelated product updates.

The correction uses Django's committed-file lifecycle: existing `FieldFile` references return without storage access; newly assigned uploads remain uncommitted and are still checked against the 6 MB limit before save. Image decoding, decompression-bomb protection, dimension limits, EXIF normalization, metadata stripping, bounded resizing, WebP conversion, randomized names, tenant-separated paths, and post-commit cleanup remain unchanged.

## Implemented controls

| Concern | Previous behavior | Implemented behavior |
|---|---|---|
| Safari backdrop blur | Standard declaration only | `-webkit-backdrop-filter` followed by `backdrop-filter` |
| Chromium/WebKit mask fade | Standard declaration only | `-webkit-mask-image` followed by `mask-image` |
| Stable bounded-scroll layout | `scrollbar-gutter: stable` | Compatible `overflow-y: scroll` on bounded regions |
| Scrollbar presentation | Thin/custom properties with matrix warnings | Accessible browser-native scrollbar |
| Regression protection | No compatibility contract | Tests require correct prefix order and reject warned scrollbar properties |
| Existing image reference | Validator queried storage on every model clean | Committed references skip upload-size I/O |
| New image upload | 6 MB validation | 6 MB validation retained |

## Verification record

The following checks are the release gate for this change:

- Python bytecode compilation: passed.
- `manage.py check`: passed with no issues.
- `manage.py makemigrations --check --dry-run`: passed; no model drift.
- Targeted CSS, product-image, and POS regression suite: 9 tests passed.
- Production `manage.py check --deploy`: passed with the single intentional HSTS preload warning. Preload remains opt-in because it is a long-lived domain-wide operational commitment, as documented by the project.
- Full Django suite: 379 tests passed after the corrections in 504.805 seconds.

The Windows test process printed WeasyPrint's native-library advisory while importing optional PDF support. It did not fail the suite. The repository Docker image already installs the documented native libraries; local Windows PDF rendering still requires the platform-specific WeasyPrint runtime if PDF output is to be exercised outside Docker.

The Django deployment checklist recommends running `check --deploy` against production settings and treating HTTPS, secrets, hosts, static/media handling, logging, and database configuration as deployment responsibilities.[^9] The current settings follow that structure; durable object storage remains a required operational step before horizontally scaling instances because the default media backend is filesystem storage.

## Prioritized follow-up roadmap

The exported Problems are fully addressed. The following are separate hardening investments rather than blockers for this patch:

1. Design and rehearse PostgreSQL RLS for classified tenant-owned tables using a least-privileged, non-bypassing request role.
2. Move product/media storage to durable object storage before multi-instance or ephemeral deployment.
3. Add the CSS compatibility contract, Django checks, migration drift check, and authorization regression suite to CI.
4. Add browser automation for Safari/WebKit, Chromium, Firefox, keyboard-only navigation, forced-colors, and 400% zoom.
5. Maintain a production deployment runbook covering backups, migration rehearsal, HSTS preload decisions, key rotation, support-session review, and denied-access alerting.

## Sources

[^1]: MDN Web Docs. "[`backdrop-filter` CSS property](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)." Accessed September 2026.
[^2]: MDN Web Docs. "[`mask-image` CSS property](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/mask-image)." Accessed September 2026.
[^3]: MDN Web Docs. "[`scrollbar-gutter` CSS property](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/scrollbar-gutter)." Accessed September 2026.
[^4]: MDN Web Docs. "[`scrollbar-width` CSS property](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/scrollbar-width)." Accessed September 2026.
[^5]: W3C Web Accessibility Initiative. "[Web Content Accessibility Guidelines (WCAG) 2.2](https://www.w3.org/TR/WCAG22/)." W3C Recommendation.
[^6]: OWASP Cheat Sheet Series. "[Multi-Tenant Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)." Accessed September 2026.
[^7]: PostgreSQL Global Development Group. "[Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)." PostgreSQL Documentation.
[^8]: Django Software Foundation. "[Managing files](https://docs.djangoproject.com/en/5.2/topics/files/)." Django 5.2 Documentation.
[^9]: Django Software Foundation. "[Deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)." Django 5.2 Documentation.
