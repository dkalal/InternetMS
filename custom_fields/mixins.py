from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .services import CustomFieldService
from users.tenancy import require_organization


class CustomFieldPageContextMixin:
    custom_field_target_model: str | None = None
    custom_field_inline_use: bool = False

    def can_manage_custom_fields(self) -> bool:
        if getattr(self.request.user, "is_superuser", False):
            return True

        role = getattr(self.request, "user_role", None)
        if role == "TENANT_ADMIN":
            return True

        membership = getattr(self.request, "membership", None)
        return getattr(membership, "role", None) in {"owner", "admin"}

    def get_custom_field_modal_context(self, *, target_model: str | None = None):
        organization = require_organization(self.request)
        model_name = target_model or self.custom_field_target_model
        modal_form, modal_open = CustomFieldService.build_inline_definition_form(
            request=self.request,
            organization=organization,
            target_model=model_name,
        )
        clean_next = self.request.get_full_path()
        split_url = urlsplit(clean_next)
        if split_url.query:
            query = [(key, value) for key, value in parse_qsl(split_url.query, keep_blank_values=True) if key != "cf_modal"]
            clean_next = urlunsplit((split_url.scheme, split_url.netloc, split_url.path, urlencode(query, doseq=True), split_url.fragment))
        return {
            "can_manage_custom_fields": self.can_manage_custom_fields(),
            "custom_field_modal_form": modal_form,
            "custom_field_modal_open": modal_open,
            "custom_field_modal_next": clean_next,
            "custom_field_organization_id": getattr(organization, "id", None),
            "custom_field_inline_use": self.custom_field_inline_use,
            "custom_field_target_model": model_name,
        }
