from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from recruiting.api.services import Services
from recruiting.identity import AuthenticationError, CurrentUser


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
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a platform user")
    return user


UserDep = Annotated[CurrentUser, Depends(current_user)]
