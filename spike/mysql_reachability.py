#!/usr/bin/env python3
"""Throwaway. Ask a GitHub runner two questions about downloads.mysql.com, using nothing a recipe
here would not have: Python 3, stdlib, no browser.

1. Can a plain client *download an asset*?  Every borrowed cell and every source build depends on it.
2. Can a plain client *resolve a catalogue* — which versions exist, and which assets a version has?
   `mariadb.py --plan` gets this from a REST API. MySQL has no such thing, and from a developer
   machine every archive URL carrying a query string answered 403 while the asset URLs answered 200.
   If that split holds on a runner, the catalogue has to come from somewhere else or be kept here.

Nothing is packed and nothing is verified. The output is a table of URL -> what happened.
"""

from __future__ import annotations

import ssl
import sys
import urllib.error
import urllib.request

TIMEOUT = 45
BARE = "Python-urllib/3"          # what a recipe sends unless it is told otherwise
BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

ARCHIVE = "https://downloads.mysql.com/archives/get/p/23/file/"

# Catalogue routes, in the order they would be preferred. Each is "how would a recipe learn what
# exists", and they differ in how much of a promise they are.
CATALOGUE = [
    # The archive's own listing — server-rendered, and the only complete catalogue of dead lines.
    ("archive files fragment", "https://downloads.mysql.com/archives/community/?tpl=files&os=33&version=9.7.1&osva="),
    ("archive version list", "https://downloads.mysql.com/archives/community/"),
    ("archive files, linux", "https://downloads.mysql.com/archives/community/?tpl=files&os=2&version=8.0.45&osva="),
    # The current-release page, which covers live lines only.
    ("dev.mysql downloads", "https://dev.mysql.com/downloads/mysql/"),
    # Machine-readable, published by Oracle for package managers. Covers live lines only, and says
    # nothing about macOS or Windows — but it is a document rather than a rendered page.
    ("yum repodata 8.0", "https://repo.mysql.com/yum/mysql-8.0-community/el/9/x86_64/repodata/repomd.xml"),
    ("yum repodata 8.4", "https://repo.mysql.com/yum/mysql-8.4-lts-community/el/9/x86_64/repodata/repomd.xml"),
    ("apt Release", "https://repo.mysql.com/apt/ubuntu/dists/jammy/Release"),
    ("apt mysql-apt-config", "https://repo.mysql.com/"),
]

# One asset per shape the design needs: a borrowed macOS ARM tarball, a borrowed Linux ARM tarball,
# a borrowed Windows zip, and the 5.6 source tarball the built cells compile.
ASSETS = [
    ("macos arm64 9.7.1", ARCHIVE + "mysql-9.7.1-macos15-arm64.tar.gz"),
    ("linux aarch64 8.0.45", ARCHIVE + "mysql-8.0.45-linux-glibc2.28-aarch64.tar.xz"),
    ("windows x86_64 9.7.1", ARCHIVE + "mysql-9.7.1-winx64.zip"),
    ("source 5.6.51", ARCHIVE + "mysql-5.6.51.tar.gz"),
    ("source 5.7.44", ARCHIVE + "mysql-5.7.44.tar.gz"),
    # Whether a signature travels beside an asset decides how the hash is verified at all: the page
    # publishes MD5, and `upstream.verified_against` wants something better than that.
    ("signature, source 5.6.51", ARCHIVE + "mysql-5.6.51.tar.gz.asc"),
    ("signature, macos 9.7.1", ARCHIVE + "mysql-9.7.1-macos15-arm64.tar.gz.asc"),
    ("gpg key", "https://repo.mysql.com/RPM-GPG-KEY-mysql-2023"),
]


def ask(url: str, agent: str, ranged: bool) -> str:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", agent)
    if ranged:
        # A recipe downloads the whole thing; a probe wants to know it *may*, not to spend the GiB.
        request.add_header("Range", "bytes=0-2047")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as response:
            body = response.read()
            length = response.headers.get("Content-Length", "?")
            kind = (response.headers.get("Content-Type") or "?").split(";")[0]
            note = ""
            if kind.startswith("text/html"):
                # A 200 that is an error page is the failure this probe exists to catch: the
                # archive answers "Technical Difficulties" with a body and not always with a status.
                text = body.decode("utf-8", "replace")
                if "Technical Difficulties" in text:
                    note = "  <- ERROR PAGE, not content"
            return f"{response.status} {kind} len={length} read={len(body)}{note}"
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code} {error.reason}"
    except Exception as error:                                    # noqa: BLE001 — a probe reports
        return f"{type(error).__name__}: {error}"


def main() -> int:
    print(f"python {sys.version.split()[0]} on {sys.platform}\n")
    for title, rows, ranged in (("CATALOGUE", CATALOGUE, False), ("ASSETS", ASSETS, True)):
        print(f"== {title} " + "=" * (60 - len(title)))
        for name, url in rows:
            print(f"\n{name}\n  {url}")
            print(f"  bare urllib UA : {ask(url, BARE, ranged)}")
            print(f"  browser UA     : {ask(url, BROWSER, ranged)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
