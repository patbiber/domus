"""Gemeinsamer Mailversand für energy/-Skripte (msmtp, Proton SMTP, Absender domus@biber.solar)."""
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

URL = "https://energy.biber.solar"


def senden(an, betreff, text, absender="homi von Biber Solar <domus@biber.solar>", reply_to="patrick@biber.solar", header=None):
    m = EmailMessage()
    m["From"] = absender
    m["To"] = an
    m["Reply-To"] = reply_to
    m["Subject"] = betreff
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain="biber.solar")
    for k, v in (header or {}).items():
        m[k] = v
    m.set_content(text)
    subprocess.run(["/usr/bin/msmtp", "-t"], input=m.as_bytes(), check=True)
