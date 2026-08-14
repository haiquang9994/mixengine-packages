"""What "this PHP works" means, in one place, for both recipes.

The two recipes have nothing else in common — one drives `static-php-cli`, the other compiles a
2016 build system — but they have to answer the same question about what they produced, or the
answer means something different depending on which branch was asked for. It did: the borrowed half
proved `php -v` and one extension, the compiled half proved eight libraries and all of them, and
nothing said so in either manifest. A weaker proof is not a smaller number in a field, it is a
different claim being made under the same name.

Nothing here starts a server or exercises an extension against one. `redis` loading is not `redis`
connecting, and this module does not pretend otherwise.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Deliberately PHP 5-era syntax: this same script has to parse on 7.0.
SCRIPT = r"""<?php
$results = array();
$results['openssl'] = strlen(openssl_digest('mixengine', 'sha256')) === 64;
$curl = curl_version();
$results['curl'] = !empty($curl['version']);
$results['mbstring'] = mb_strtoupper('mixengine') === 'MIXENGINE';
$results['intl'] = numfmt_format(numfmt_create('en_US', NumberFormatter::DECIMAL), 1234.5) !== false;
$image = imagecreatetruecolor(1, 1);
$results['gd'] = !empty($image);
$results['zip'] = class_exists('ZipArchive');
$database = new SQLite3(':memory:');
$results['sqlite3'] = $database->querySingle('select 1') == 1;
$xml = simplexml_load_string('<a><b>c</b></a>');
$results['xml'] = $xml && (string) $xml->b === 'c';
$failed = array();
foreach ($results as $name => $ok) { if (!$ok) { $failed[] = $name; } }
echo $failed ? 'FAILED: ' . implode(',', $failed) : 'OK';
"""

# Loaded with `zend_extension=` rather than `extension=`. Getting this wrong does not look like a
# configuration mistake from the outside — the extension simply reports as not loaded, which is
# indistinguishable from a broken build. `opcache` is here as well as `xdebug` because PHP builds it
# as a shared module by default, so it arrives in `ext/` alongside the PECL ones.
ZEND_EXTENSIONS = {"xdebug", "opcache"}

# What `extension_loaded()` answers to, where that is not the file name. Only opcache so far, and
# missing it makes a perfectly loaded extension report as absent.
EXTENSION_NAMES = {"opcache": "Zend OPcache"}


def libraries(php: Path, script: Path) -> str:
    """Call into every bundled library and compare the answer, rather than ask whether it is there.

    `function_exists` passes on a build whose libraries were left behind: the symbol is linked in
    and the library it needs is not, which is a failure at call time and only at call time.
    """
    script.write_text(SCRIPT, encoding="utf-8")
    attempt = subprocess.run(
        [str(php), "-n", str(script)], capture_output=True, text=True, timeout=300
    )
    return (attempt.stdout.strip() or attempt.stderr.strip()).strip()


def loads(php: Path, extension_dir: Path, module: str, ini: Path) -> tuple[bool, str, str]:
    """Try to load one extension through a generated ini, and report what PHP said about it.

    The ini is the mechanism the daemon will use, so it is the one worth proving — and
    ``display_startup_errors`` is turned on because loading an extension happens at startup, where
    PHP's default is to refuse in silence. A refusal nobody can see is the failure this whole check
    exists to catch.
    """
    name = EXTENSION_NAMES.get(module, module)
    directive = "zend_extension" if module in ZEND_EXTENSIONS else "extension"
    lines = ["display_errors=stderr\n", "display_startup_errors=On\n", "error_reporting=E_ALL\n",
             f'extension_dir="{extension_dir}"\n']
    if module == "redis" and (extension_dir / "igbinary.so").exists():
        lines.append(f'extension="{extension_dir / "igbinary.so"}"\n')
    lines.append(f'{directive}="{extension_dir / (module + ".so")}"\n')
    ini.write_text("".join(lines), encoding="utf-8")

    attempt = subprocess.run(
        [str(php), "-c", str(ini), "-r", f"echo extension_loaded({name!r}) ? 'yes' : 'no';"],
        capture_output=True, text=True, timeout=300,
    )
    ok = attempt.stdout.strip().endswith("yes")
    error = attempt.stderr.strip()
    if not ok and not error:
        # PHP refusing an extension without a word means one of exactly two things, and `dl()` says
        # which. It reports "dynamic modules are not supported" when PHP was built without
        # HAVE_LIBDL — in which case `extension=` lines are not ignored so much as compiled out of
        # existence, since both loader callbacks in main/php_ini.c have empty bodies without it.
        # Otherwise it reports dlopen's own complaint, which is the answer we were looking for all
        # along and which the ini path never shows.
        probe = subprocess.run(
            [str(php), "-c", str(ini), "-r", f"var_dump(dl({module + '.so'!r}));"],
            capture_output=True, text=True, timeout=300,
        )
        error = "dl() says: " + " ".join(
            (probe.stdout + " " + probe.stderr).split()
        )
    return ok, attempt.stdout.strip(), error
