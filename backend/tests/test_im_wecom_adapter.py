"""企业微信适配器测试。"""

import json
from urllib.parse import urlencode

import pytest
from starlette.datastructures import MutableHeaders

from app.integrations.im.adapters.wecom import WeComAdapter
from app.integrations.im.schemas import (
    ChatType,
    IncomingMessage,
    MessageType,
    Platform,
    ReplyMessage,
)


class MockQueryParams:
    """模拟 Request.query_params。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self._data[key]


class MockRequest:
    """模拟 FastAPI Request。"""

    def __init__(
        self,
        body: bytes = b"",
        headers: dict | None = None,
        query: dict | None = None,
        method: str = "GET",
    ):
        self._body = body
        self.headers = MutableHeaders(headers or {})
        self.query_params = MockQueryParams(query or {})
        self.method = method

    async def body(self) -> bytes:
        return self._body

    @property
    def query_params_string(self) -> str:
        return urlencode(self.query_params._data)


def build_url_verification_query(token: str, echostr: str) -> dict:
    """构造企业微信 URL 验证查询参数。"""
    import hashlib
    import time

    timestamp = str(int(time.time()))
    nonce = "nonce123"
    tmp = "".join(sorted([token, timestamp, nonce, echostr]))
    signature = hashlib.sha1(tmp.encode()).hexdigest()
    return {
        "signature": signature,
        "timestamp": timestamp,
        "nonce": nonce,
        "echostr": echostr,
    }


class TestWeComAdapterInit:
    """企业微信适配器初始化测试。"""

    def test_should_init_with_valid_credentials(self):
        """应能用有效凭据初始化。"""
        adapter = WeComAdapter(
            credentials={
                "corp_id": "corp-123",
                "agent_secret": "secret-456",
                "token": "token-789",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                "corp_agent_id": 1000001,
            }
        )

        assert adapter.platform == Platform.WECOM
        assert adapter.corp_id == "corp-123"
        assert adapter.corp_agent_id == 1000001


class TestWeComAdapterURLVerification:
    """企业微信 URL 验证测试。"""

    @pytest.fixture
    def adapter(self):
        return WeComAdapter(
            credentials={
                "corp_id": "corp-123",
                "agent_secret": "secret-456",
                "token": "test-token",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                "corp_agent_id": 1000001,
            }
        )

    def test_should_verify_url_and_return_echostr(self, adapter):
        """URL 验证通过时应返回解密后的 echostr。"""
        echostr = "hello-wecom"
        encrypted = adapter._encrypt(echostr)  # type: ignore[attr-defined]
        query = build_url_verification_query("test-token", encrypted)
        request = MockRequest(query=query)

        result = adapter.handle_url_verification_sync(request)

        assert result == echostr

    def test_should_reject_invalid_signature(self, adapter):
        """签名错误时应返回空字符串。"""
        query = {
            "signature": "invalid",
            "timestamp": "1234567890",
            "nonce": "nonce123",
            "echostr": "hello-wecom",
        }
        request = MockRequest(query=query)

        result = adapter.handle_url_verification_sync(request)

        assert result == ""


class TestWeComAdapterParseCallback:
    """企业微信回调消息解析测试。"""

    @pytest.fixture
    def adapter(self):
        return WeComAdapter(
            credentials={
                "corp_id": "corp-123",
                "agent_secret": "secret-456",
                "token": "test-token",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                "corp_agent_id": 1000001,
            }
        )

    def test_should_parse_text_message(self, adapter):
        """应能解析私聊文本消息。"""
        xml_msg = """<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <FromUserName><![CDATA[from_user_123]]></FromUserName>
            <CreateTime>1234567890</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[我们能投这个标吗？]]></Content>
            <MsgId>msg_id_123</MsgId>
            <AgentID>1000001</AgentID>
        </xml>"""
        encrypted = adapter._encrypt(xml_msg)  # type: ignore[attr-defined]
        body_xml = f"""<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <Encrypt><![CDATA[{encrypted}]]></Encrypt>
            <AgentID><![CDATA[1000001]]></AgentID>
        </xml>"""

        import hashlib
        import time

        timestamp = str(int(time.time()))
        nonce = "nonce123"
        tmp = "".join(sorted(["test-token", timestamp, nonce, encrypted]))
        signature = hashlib.sha1(tmp.encode()).hexdigest()
        request = MockRequest(
            body=body_xml.encode("utf-8"),
            query={"msg_signature": signature, "timestamp": timestamp, "nonce": nonce},
            method="POST",
        )

        msg = adapter.parse_callback_sync(request)

        assert msg is not None
        assert msg.platform == Platform.WECOM
        assert msg.message_type == MessageType.TEXT
        assert msg.user_id == "from_user_123"
        assert msg.chat_id == ""
        assert msg.chat_type == ChatType.DIRECT
        assert msg.content == "我们能投这个标吗？"
        assert msg.message_id == "msg_id_123"

    def test_should_parse_group_text_message_with_mention_stripped(self, adapter):
        """应能解析群聊文本消息并剥除 @机器人 前缀。"""
        xml_msg = """<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <FromUserName><![CDATA[from_user_123]]></FromUserName>
            <CreateTime>1234567890</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[@机器人 请分析这个标]]></Content>
            <MsgId>msg_id_456</MsgId>
            <ChatId><![CDATA[chat_789]]></ChatId>
            <AgentID>1000001</AgentID>
        </xml>"""
        encrypted = adapter._encrypt(xml_msg)  # type: ignore[attr-defined]
        body_xml = f"""<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <Encrypt><![CDATA[{encrypted}]]></Encrypt>
            <AgentID><![CDATA[1000001]]></AgentID>
        </xml>"""

        import hashlib
        import time

        timestamp = str(int(time.time()))
        nonce = "nonce123"
        tmp = "".join(sorted(["test-token", timestamp, nonce, encrypted]))
        signature = hashlib.sha1(tmp.encode()).hexdigest()
        request = MockRequest(
            body=body_xml.encode("utf-8"),
            query={"msg_signature": signature, "timestamp": timestamp, "nonce": nonce},
            method="POST",
        )

        msg = adapter.parse_callback_sync(request)

        assert msg is not None
        assert msg.chat_type == ChatType.GROUP
        assert msg.chat_id == "chat_789"
        assert msg.content == "请分析这个标"

    def test_should_return_none_for_unsupported_message_type(self, adapter):
        """不支持的消息类型应返回 None。"""
        xml_msg = """<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <FromUserName><![CDATA[from_user_123]]></FromUserName>
            <CreateTime>1234567890</CreateTime>
            <MsgType><![CDATA[location]]></MsgType>
            <MsgId>msg_id_789</MsgId>
            <AgentID>1000001</AgentID>
        </xml>"""
        encrypted = adapter._encrypt(xml_msg)  # type: ignore[attr-defined]
        body_xml = f"""<xml>
            <ToUserName><![CDATA[to_user]]></ToUserName>
            <Encrypt><![CDATA[{encrypted}]]></Encrypt>
            <AgentID><![CDATA[1000001]]></AgentID>
        </xml>"""

        import hashlib
        import time

        timestamp = str(int(time.time()))
        nonce = "nonce123"
        tmp = "".join(sorted(["test-token", timestamp, nonce, encrypted]))
        signature = hashlib.sha1(tmp.encode()).hexdigest()
        request = MockRequest(
            body=body_xml.encode("utf-8"),
            query={"msg_signature": signature, "timestamp": timestamp, "nonce": nonce},
            method="POST",
        )

        msg = adapter.parse_callback_sync(request)

        assert msg is None


class TestWeComAdapterSendReply:
    """企业微信发送回复测试。"""

    @pytest.mark.asyncio
    async def test_should_send_text_reply_to_user(self, httpx_mock, monkeypatch):
        """应能调用企业微信 API 给用户发送文本回复。"""
        # 单元测试不读取开发环境 Redis 中残留的真实 token。
        monkeypatch.setattr(
            "app.integrations.im.adapters.wecom._get_redis_client", lambda: None
        )
        httpx_mock.add_response(
            url="https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=corp-123&corpsecret=secret-456",
            json={"errcode": 0, "errmsg": "ok", "access_token": "token-abc", "expires_in": 7200},
        )
        httpx_mock.add_response(
            url="https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=token-abc",
            json={"errcode": 0, "errmsg": "ok"},
        )

        adapter = WeComAdapter(
            credentials={
                "corp_id": "corp-123",
                "agent_secret": "secret-456",
                "token": "test-token",
                "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                "corp_agent_id": 1000001,
            }
        )

        incoming = IncomingMessage(
            platform=Platform.WECOM,
            message_type=MessageType.TEXT,
            user_id="user-123",
            chat_id="",
            content="question",
            message_id="msg-100",
        )
        reply = ReplyMessage(content="answer")

        await adapter.send_reply(incoming, reply)

        request = httpx_mock.get_requests()[-1]
        body = json.loads(request.content.decode())
        assert body["touser"] == "user-123"
        assert body["agentid"] == 1000001
        assert body["msgtype"] == "markdown"
        assert body["markdown"]["content"] == "answer"
