# Post-login workspace audit

## Finding

The prior login configuration set `LOGIN_REDIRECT_URL = 'customer-list'`. Django `LoginView` therefore sent every normal sign-in to `/customers/`. The root URL also redirected to the login page unconditionally.

This made a customer table function as the application home. That is unsuitable for a multi-role B2B system: it skips orientation and prioritization for Managers, exposes a broad customer task immediately to Sales, sends Technicians to a route they are not permitted to use, and gives Super Administrators no deliberate support-context decision.

The previous login templates also did not render the hidden `next` field. As a result, an unauthenticated user sent to login from a protected deep link could lose their intended destination when submitting the form.

## Design decision

The root URL is now the authenticated workspace entry. Django continues to validate the `next` parameter; a safe deep link takes precedence. When there is no `next` value, `WorkspaceLoginView` uses the role-aware root fallback.

| Role | Default entry | Why |
|---|---|---|
| Super Administrator | Support-context selection | A tenant is never selected implicitly; context remains explicit and audited. |
| Administrator / Manager | Operations overview | A focused overview of customer/service volume, own-tenant commercial work, and Work Report approvals. |
| Sales | My sales workspace | Own/assigned document pipeline and immediate actions, without tenant-wide totals or financial KPIs. |
| Technician | My Work Reports | The only operational domain the Technician is permitted to use. |

## Implementation

- `users.auth_views.WorkspaceLoginView` preserves Django's safe redirect handling and provides a workspace fallback.
- `main_app.workspace_home` enforces role routing server-side; template visibility is not the authorization control.
- The Manager/Sales workspace uses permission-scoped document querysets. Sales sees no aggregate customer/service counts, finance totals, inventory launch action, Work Report approvals, or other users' documents.
- Login templates retain `next` across form POSTs.
- The sidebar logo now returns to the workspace home instead of customer list.

## Research principles applied

- Django authentication documents that `next` takes precedence over `LOGIN_REDIRECT_URL`; preserving it maintains deep-link continuity and relies on Django's safe-host validation.
- The navigation and dashboard use task priority rather than organizational structure: the first page presents the role's most common allowed work, then direct actions and recent items.
- The layout is intentionally summary-first, restrained, accessible, and responsive; detailed operational tables remain in their dedicated modules.

## Validation

Automated tests cover Manager, Sales, Technician, Super Administrator, protected deep-link return, and rejection of an external redirect target. Manual verification should use one account from each role and confirm both plain-login and deep-link-login behavior.
