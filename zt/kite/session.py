"""Daily Kite session: the access token lives only in data/session.json (mode 0600)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from zt.config import Config
from zt.core import clock


class SessionError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    date: str
    access_token: str
    user_id: str
    login_at: int
    generation: int

    @property
    def is_today(self) -> bool:
        return self.date == clock.today().isoformat()


def load(path: Path) -> Session | None:
    if not path.exists():
        return None
    return Session(**json.loads(path.read_text()))


def save(path: Path, session: Session) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".session-")
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(session), f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def login_url(cfg: Config) -> str:
    return KiteConnect(api_key=cfg.api_key).login_url()


def login(cfg: Config, request_token: str) -> Session:
    kc = KiteConnect(api_key=cfg.api_key)
    data = kc.generate_session(request_token=request_token, api_secret=cfg.api_secret)
    previous = load(cfg.session_file)
    session = Session(
        date=clock.today().isoformat(),
        access_token=data["access_token"],
        user_id=data["user_id"],
        login_at=clock.to_epoch(clock.now()),
        generation=(previous.generation + 1) if previous else 1,
    )
    save(cfg.session_file, session)
    return session


def client(cfg: Config) -> KiteConnect:
    """KiteConnect bound to today's token, or SessionError telling the operator to log in."""
    session = load(cfg.session_file)
    if session is None or not session.is_today:
        raise SessionError("no Kite session for today: run `zt login` and paste the request_token")
    kc = KiteConnect(api_key=cfg.api_key)
    kc.set_access_token(session.access_token)
    return kc


def verify(kc: KiteConnect) -> str | None:
    """user_id when the token is accepted, None when Kite rejects it."""
    try:
        return kc.profile()["user_id"]
    except TokenException:
        return None
