from dataclasses import dataclass, field

from integrations.iiko.schemas import AuthKind


@dataclass(frozen=True)
class AuthConfiguration:
    kind: AuthKind = AuthKind.UNKNOWN
    username: str | None = None
    password: str | None = field(default=None, repr=False, compare=False)
    api_login: str | None = field(default=None, repr=False, compare=False)
    token: str | None = field(default=None, repr=False, compare=False)

    @property
    def is_configured(self) -> bool:
        if self.kind is AuthKind.NONE:
            return True
        if self.kind is AuthKind.USER_PASSWORD:
            return bool(self.username and self.password)
        if self.kind is AuthKind.API_LOGIN:
            return bool(self.api_login and self.password)
        if self.kind is AuthKind.BEARER_TOKEN:
            return bool(self.token)
        return False

    def sanitized_metadata(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "configured": self.is_configured,
            "username_configured": bool(self.username),
            "password_configured": bool(self.password),
            "api_login_configured": bool(self.api_login),
            "token_configured": bool(self.token),
        }
