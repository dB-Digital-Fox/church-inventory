import io
import re

import pytest
from PIL import Image

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SCHEDULER": False,
                      "SECRET_KEY": "test", "BASE_URL": "http://church.test"})
    yield app


class Client:
    """Test client that follows the CSRF token automatically."""

    def __init__(self, app):
        self.app = app
        self.c = app.test_client()

    def token(self):
        with self.c.session_transaction() as s:
            if "csrf" not in s:
                s["csrf"] = "tok"
            return s["csrf"]

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.token())
        return self.c.post(url, data=data, **kw)

    def login(self, username, password):
        return self.post("/login", {"username": username, "password": password})

    def logout(self):
        return self.post("/logout")


@pytest.fixture()
def client(app):
    return Client(app)


ADMIN_PW = "correct horse battery"


@pytest.fixture()
def admin(client):
    r = client.post("/setup", {"username": "admin", "full_name": "Admin Person",
                               "password": ADMIN_PW, "password2": ADMIN_PW, "language": "en"})
    assert r.status_code == 302
    return client


def make_user(admin_client, username, role, full_name=None):
    """Create a user as admin and return its temporary password."""
    r = admin_client.post("/people/new", {"username": username, "full_name": full_name or username,
                                          "role": role, "language": "en"})
    assert r.status_code == 200
    return re.search(r'<code class="big">([^<]+)</code>', r.get_data(as_text=True)).group(1)


def login_as(app, admin_client, username, role):
    temp = make_user(admin_client, username, role)
    c = Client(app)
    c.login(username, temp)
    new_pw = "new password 123"
    r = c.post("/profile", {"action": "password", "current": temp, "password": new_pw,
                            "password2": new_pw})
    assert r.status_code == 302
    return c


def jpeg_bytes(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    buf.seek(0)
    return buf
