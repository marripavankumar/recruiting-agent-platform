from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status

from recruiting.api.services import Services
from recruiting.domain import Role
from recruiting.identity import AuthenticationError, CurrentUser

if TYPE_CHECKING:
    from recruiting.api.platform import Platform


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def current_user(request: Request, services: ServicesDep) -> CurrentUser:
    try:
        email = services.authenticator.identify(request.headers)
    except AuthenticationError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    user = services.users.by_email(email)
    request.state.user = user  # for the access log (scenario S26)
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a platform user")
    return user


UserDep = Annotated[CurrentUser, Depends(current_user)]


def require_role(user: CurrentUser, roles: Iterable[Role]) -> None:
    allowed = tuple(roles)
    if user.role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot use this function")


def require_platform(services: Services) -> Platform:
    if services.platform is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Business services are not configured"
        )
    return services.platform
