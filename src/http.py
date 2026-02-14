

import threading
from typing import Any

import requests


class ThreadLocalSessionMixin:
    

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._session_local = threading.local()
        super().__init__(*args, **kwargs)

    def _get_session(self) -> requests.Session:
        
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = requests.Session()
            self._session_local.session = session
        return session

    @property
    def session(self) -> requests.Session:
        
        return self._get_session()
