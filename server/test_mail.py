"""Mail tests: fake SMTP, no network (stdlib + pytest only)."""
from __future__ import annotations

import smtplib
import socket
import ssl

import pytest

from server import mail, mailtest
from server.config import MailConfigError, load_smtp_config

PW = "S3cr3t-P@ss!"
ENV = {"SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587", "SMTP_USER": "box@example.test",
       "SMTP_PASSWORD": PW, "MAIL_FROM": "alias@example.test"}


class FakeSMTP:
    instances: list = []
    starttls_offered = True
    fail: dict = {}

    def __init__(self, host, port, timeout=None):
        self.calls, self.timeout, self.sent = [], timeout, None
        if "connect" in self.fail:
            raise self.fail["connect"]
        FakeSMTP.instances.append(self)

    def ehlo(self): self.calls.append("ehlo")
    def has_extn(self, n): return n.lower() == "starttls" and self.starttls_offered
    def starttls(self, context=None):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.calls.append("starttls")
        if "starttls" in self.fail:
            raise self.fail["starttls"]
    def login(self, u, p):
        self.calls.append("login")
        if "login" in self.fail:
            raise self.fail["login"]
    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append("send")
        self.sent = (msg, from_addr, to_addrs)
    def quit(self): self.calls.append("quit")
    def close(self): self.calls.append("close")


@pytest.fixture(autouse=True)
def reset():
    FakeSMTP.instances, FakeSMTP.starttls_offered, FakeSMTP.fail = [], True, {}


def cfg(**extra):
    return load_smtp_config({**ENV, **extra})


def test_missing_variable_names_only_the_name():
    env = dict(ENV)
    del env["SMTP_PASSWORD"]
    with pytest.raises(MailConfigError) as e:
        load_smtp_config(env)
    assert "SMTP_PASSWORD" in str(e.value) and PW not in str(e.value)


def test_defaults_and_repr_hide_password():
    c = cfg()
    assert c.timeout == 15 and c.envelope_from == ""
    assert PW not in repr(c) and PW not in str(c)


def test_bad_port_rejected():
    with pytest.raises(MailConfigError):
        cfg(SMTP_PORT="abc")


def test_call_order_and_timeout():
    mail.send_mail(cfg(SMTP_TIMEOUT="7"), "to@example.org", "Hi", "Body", smtp_factory=FakeSMTP)
    s = FakeSMTP.instances[0]
    assert s.calls == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert s.timeout == 7


def test_message_headers_and_envelope_fallback():
    mail.send_mail(cfg(MAIL_FROM_NAME="Virtual Captain"), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    msg, frm, to = FakeSMTP.instances[0].sent
    assert frm == "alias@example.test" and to == ["to@example.org"]
    assert "Reply-To" not in msg and "alias@example.test" in msg["From"]
    assert msg["Message-ID"].endswith("@example.test>") and msg["Date"]
    mail.send_mail(cfg(MAIL_ENVELOPE_FROM="box@example.test"), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    msg, frm, _ = FakeSMTP.instances[1].sent
    assert frm == "box@example.test" and msg["Reply-To"] == "alias@example.test"
    assert msg["From"] == "alias@example.test"


def test_starttls_not_offered_refuses_before_login():
    FakeSMTP.starttls_offered = False
    with pytest.raises(mail.MailSendError) as e:
        mail.send_mail(cfg(), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    assert e.value.kind == "tls"
    assert "login" not in FakeSMTP.instances[0].calls


@pytest.mark.parametrize("to,subj", [("a@b.it\nBcc: x@y.it", "s"), ("a@b.it", "s\r\nBcc: x@y.it")])
def test_newlines_rejected(to, subj):
    with pytest.raises(MailConfigError):
        mail.send_mail(cfg(), to, subj, "B", smtp_factory=FakeSMTP)
    assert not FakeSMTP.instances


@pytest.mark.parametrize("exc,kind", [
    (smtplib.SMTPAuthenticationError(535, b"bad " + PW.encode()), "auth"),
    (ssl.SSLCertVerificationError("cert"), "tls"),
    (socket.timeout("t"), "timeout"),
    (ConnectionRefusedError("no"), "connection"),
    (smtplib.SMTPServerDisconnected("x"), "connection"),
    (smtplib.SMTPNotSupportedError("x"), "connection"),
    (smtplib.SMTPSenderRefused(550, b"no " + PW.encode(), "a@b.it"), "sender"),
    (smtplib.SMTPRecipientsRefused({"a@b.it": (550, b"x")}), "recipient"),
    (smtplib.SMTPDataError(554, b"x"), "recipient"),
    (RuntimeError("boom " + PW), "internal"),
])
def test_errors_mapped_without_password(exc, kind):
    FakeSMTP.fail = {"login": exc}
    with pytest.raises(mail.MailSendError) as e:
        mail.send_mail(cfg(), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    assert e.value.kind == kind
    assert PW not in str(e.value) and PW not in repr(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_connect_timeout_mapped():
    FakeSMTP.fail = {"connect": socket.timeout("t")}
    with pytest.raises(mail.MailSendError) as e:
        mail.send_mail(cfg(), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    assert e.value.kind == "timeout"


def test_mailtest_ok_and_check(capsys):
    assert mailtest.main(["to@example.org"], env=ENV, smtp_factory=FakeSMTP) == 0
    assert "send" in FakeSMTP.instances[0].calls
    assert mailtest.main(["--check"], env=ENV, smtp_factory=FakeSMTP) == 0
    assert "send" not in FakeSMTP.instances[1].calls
    out = capsys.readouterr()
    assert out.out.split() == ["OK", "OK"] and PW not in out.out + out.err


def test_mailtest_envelope_option():
    mailtest.main(["to@example.org", "--envelope-from", "box@example.test"], env=ENV, smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].sent[1] == "box@example.test"


@pytest.mark.parametrize("argv,env,fail,code,word", [
    (["to@example.org"], {}, {}, 2, "config"),
    (["not-an-address"], ENV, {}, 2, "config"),
    (["to@example.org"], ENV, {"login": smtplib.SMTPAuthenticationError(535, b"x")}, 3, "auth"),
    (["to@example.org"], ENV, {"login": socket.timeout()}, 3, "timeout"),
])
def test_mailtest_errors(capsys, argv, env, fail, code, word):
    FakeSMTP.fail = fail
    assert mailtest.main(argv, env=env, smtp_factory=FakeSMTP) == code
    out = capsys.readouterr()
    assert word in out.err and out.out == "" and PW not in out.out + out.err


def test_non_ascii_password_rejected_without_leaking():
    with pytest.raises(MailConfigError) as e:
        cfg(SMTP_PASSWORD="pa\u00e8ss-segreta")
    assert "pa" not in str(e.value).replace("SMTP_PASSWORD", "") and "segreta" not in str(e.value)


def test_password_newline_rejected():
    with pytest.raises(MailConfigError) as e:
        cfg(SMTP_PASSWORD="abc\ndef")
    assert "abc" not in str(e.value)


@pytest.mark.parametrize("t", ["nan", "inf", "0", "-1"])
def test_bad_timeout(t):
    with pytest.raises(MailConfigError):
        cfg(SMTP_TIMEOUT=t)


@pytest.mark.parametrize("addr", ["a@b.it,c@d.it", "Nome <x@y.it>", "a@b.it;c@d.it", "j\u00f2@b.it", "x@y"])
def test_recipient_validation_in_send_mail(addr):
    with pytest.raises(MailConfigError):
        mail.send_mail(cfg(), addr, "Hi", "B", smtp_factory=FakeSMTP)
    assert not FakeSMTP.instances


@pytest.mark.parametrize("k,v", [("MAIL_FROM", "Name <a@b.it>"), ("MAIL_FROM", "nodomain"),
                                 ("MAIL_ENVELOPE_FROM", "a@b.it,c@d.it")])
def test_from_addresses_validated(k, v):
    with pytest.raises(MailConfigError):
        cfg(**{k: v})


def test_starttls_failure_closes_session():
    FakeSMTP.fail = {"starttls": smtplib.SMTPException("x")}
    with pytest.raises(mail.MailSendError):
        mail.send_mail(cfg(), "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].calls[-1] == "quit" and "login" not in FakeSMTP.instances[0].calls


def test_reply_to_with_from_name():
    mail.send_mail(cfg(MAIL_FROM_NAME="VC", MAIL_ENVELOPE_FROM="box@example.test"),
                   "to@example.org", "Hi", "B", smtp_factory=FakeSMTP)
    msg = FakeSMTP.instances[0].sent[0]
    assert msg["Reply-To"] == "alias@example.test" and "VC" in msg["From"]


def test_check_with_dash_recipient(capsys):
    assert mailtest.main(["-", "--check"], env=ENV, smtp_factory=FakeSMTP) == 0


def test_mailtest_non_ascii_password_and_internal(capsys):
    env = {**ENV, "SMTP_PASSWORD": "p\u00e8ssword"}
    assert mailtest.main(["to@example.org"], env=env, smtp_factory=FakeSMTP) == 2
    out = capsys.readouterr()
    assert "ssword" not in out.out + out.err
    FakeSMTP.fail = {"login": RuntimeError("boom " + PW)}
    assert mailtest.main(["to@example.org"], env=ENV, smtp_factory=FakeSMTP) == 3
    out = capsys.readouterr()
    assert "ERROR internal" in out.err and PW not in out.out + out.err and "Traceback" not in out.err


@pytest.mark.parametrize("exc,word", [(smtplib.SMTPSenderRefused(550, b"x", "a@b.it"), "sender"),
                                      (smtplib.SMTPRecipientsRefused({}), "recipient")])
def test_mailtest_sender_recipient(capsys, exc, word):
    FakeSMTP.fail = {"login": exc}
    assert mailtest.main(["to@example.org"], env=ENV, smtp_factory=FakeSMTP) == 3
    assert f"ERROR {word}" in capsys.readouterr().err
