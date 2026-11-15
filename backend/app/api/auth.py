"""Signup, login, logout and the current user (DECISIONS.md D14)."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, StringConstraints

from app.api.deps import UserDep
from app.services import accounts

router = APIRouter(prefix="/api/auth", tags=["auth"])

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=200)]


class SignupIn(BaseModel):
    org_name: Name
    name: Name
    email: EmailStr
    password: Password


class LoginIn(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        accounts.COOKIE_NAME,
        token,
        max_age=int(accounts.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.is_prod,
        path="/",
    )


@router.post("/signup", status_code=201)
async def signup(body: SignupIn, request: Request, response: Response) -> dict[str, Any]:
    db = request.app.state.db.db
    try:
        user = await accounts.create_account(
            db, org_name=body.org_name, name=body.name, email=body.email, password=body.password
        )
    except accounts.EmailTaken as exc:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists. Log in instead."
        ) from exc
    token = await accounts.create_session(db, request.app.state.settings, user)
    _set_cookie(request, response, token)
    return user.public()


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    throttle: accounts.LoginThrottle = request.app.state.login_throttle
    if throttle.blocked(body.email):
        raise HTTPException(
            status_code=429, detail="Too many failed attempts. Wait 15 minutes and try again."
        )
    db = request.app.state.db.db
    user = await accounts.authenticate(db, body.email, body.password)
    if user is None:
        throttle.failed(body.email)
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    throttle.succeeded(body.email)
    token = await accounts.create_session(db, request.app.state.settings, user)
    _set_cookie(request, response, token)
    return user.public()


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(accounts.COOKIE_NAME, "")
    await accounts.delete_session(request.app.state.db.db, request.app.state.settings, token)
    response.delete_cookie(accounts.COOKIE_NAME, path="/")


@router.get("/me")
async def me(user: UserDep) -> dict[str, Any]:
    return user.public()
