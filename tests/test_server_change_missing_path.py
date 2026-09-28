import pytest

from archives_application import bcrypt, create_app, db
from archives_application.models import UserModel


class TestConfig:
    SECRET_KEY = "server-change-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "server-change-test-client"
    REDIS_URL = "redis://localhost:6379/0"
    USER_ARCHIVES_LOCATION = r"N:\PPDO"


@pytest.fixture()
def client(tmp_path):
    app = create_app(TestConfig)
    app.config["ARCHIVES_LOCATION"] = str(tmp_path)
    with app.app_context():
        UserModel.__table__.create(db.engine)
        db.session.add(UserModel(
            email="archivist@example.org",
            roles="ARCHIVIST",
            password=bcrypt.generate_password_hash("secret").decode(),
        ))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        UserModel.__table__.drop(db.engine)


def test_missing_server_change_source_returns_404_with_entered_path(client):
    entered_path = r"N:\PPDO\Records\missing.pdf"
    response = client.post("/api/server_change", json={
        "user": "archivist@example.org",
        "password": "secret",
        "edit_type": "DELETE",
        "old_path": entered_path,
    })

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json() == {
        "error": "Source path does not exist",
        "path": entered_path,
    }
    assert b"Stack trace" not in response.data
