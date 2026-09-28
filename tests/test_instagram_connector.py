# SPDX-License-Identifier: Apache-2.0
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from connectors.instagram import InstagramConnector  # noqa: E402


def connector():
    with mock.patch.dict(os.environ, {"INSTAGRAM_ACCESS_TOKEN": "secret",
                                      "INSTAGRAM_USER_ID": "1784"}, clear=False):
        return InstagramConnector()


def test_classification_matches_tools():
    c = connector()
    names = [x["function"]["name"] for x in c.get_tools()]
    assert c.classification_problems(names) == []
    assert c.tool_kind("instagram_publish_image") == "write"
    assert c.tool_kind("instagram_send_message") == "write"
    assert c.tool_kind("instagram_add_comment") == "write"
    assert c.tool_kind("instagram_send_image") == "write"


def test_publish_rejects_non_https_without_network():
    c = connector()
    with mock.patch("connectors.instagram.requests.request") as request:
        result = c.execute("instagram_publish_image",
                           {"image_url": "http://localhost/a.jpg", "caption": "sogno"})
    assert "HTTPS pubblico" in result
    request.assert_not_called()


def test_account_status_does_not_expose_token():
    response = mock.Mock(status_code=200)
    response.json.return_value = {"username": "aurora", "account_type": "BUSINESS",
                                  "user_id": "1784"}
    with mock.patch("connectors.instagram.requests.request", return_value=response):
        result = connector().execute("instagram_account_status", {})
    assert "aurora" in result
    assert "secret" not in result


def test_send_message_uses_scoped_recipient_id():
    response = mock.Mock(status_code=200)
    response.json.return_value = {"recipient_id": "123", "message_id": "mid.1"}
    with mock.patch("connectors.instagram.requests.request", return_value=response) as request:
        result = connector().execute("instagram_send_message",
                                     {"recipient_id": "123", "text": "Ciao!"})
    assert '"message_id": "mid.1"' in result
    assert request.call_args.kwargs["json"] == {
        "recipient": {"id": "123"}, "message": {"text": "Ciao!"}}


def test_add_comment_posts_text_to_media():
    response = mock.Mock(status_code=200)
    response.json.return_value = {"id": "comment.1"}
    with mock.patch("connectors.instagram.requests.request", return_value=response) as request:
        result = connector().execute("instagram_add_comment",
                                     {"media_id": "456", "text": "Una poesia"})
    assert '"comment_id": "comment.1"' in result
    assert request.call_args.args[1].endswith("/456/comments")
    assert request.call_args.kwargs["data"] == {"message": "Una poesia"}


def test_send_image_requires_https_and_builds_attachment():
    response = mock.Mock(status_code=200)
    response.json.return_value = {"recipient_id": "123", "message_id": "img.1"}
    with mock.patch("connectors.instagram.requests.request", return_value=response) as request:
        result = connector().execute("instagram_send_image", {
            "recipient_id": "123", "image_url": "https://example.test/drawing.jpg"})
    assert '"message_id": "img.1"' in result
    payload = request.call_args.kwargs["json"]
    assert payload["message"]["attachment"]["payload"]["url"].startswith("https://")
