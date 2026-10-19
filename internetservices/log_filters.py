"""Remove invitation bearer tokens from request diagnostics."""
import logging
import re


class RedactInvitationTokens(logging.Filter):
    pattern = re.compile(r"/users/invitation/[^/?\s]+/")

    def filter(self, record):
        message = record.getMessage()
        if "/users/invitation/" in message:
            record.msg = self.pattern.sub("/users/invitation/[redacted]/", message)
            record.args = ()
        return True


def redact_invitation_path(path):
    return RedactInvitationTokens.pattern.sub("/users/invitation/[redacted]/", path)
