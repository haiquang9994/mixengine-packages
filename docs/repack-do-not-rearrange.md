# Repack, do not rearrange

*Part of [mixengine-packages](../README.md), which holds the table of what is packaged.*

A borrowed artifact keeps the directory layout its publisher shipped. It is tempting to normalise
every runtime into one `bin/`, `lib/`, `ext/` shape so the daemon needs no per-OS knowledge — and it
would break Windows immediately, where `php.exe` resolves its DLLs from its own directory and moving
them apart makes the binary unloadable in a way that only shows up at run time.

So the abstraction is not the directory. It is **`mixengine-artifact.json`**, written into the root
of every archive, which names where things actually are:

```json
{
  "schema": 1,
  "kind": "php", "version": "8.3.33", "os": "windows", "arch": "x86_64",
  "source": "borrowed",
  "provides": { "php": "php.exe", "php-cgi": "php-cgi.exe" },
  "extension_dir": "ext",
  "extensions": { "static": ["Core", "openssl", "..."], "shared": ["curl", "..."] },
  "requires": { "vcredist": "2019" }
}
```

The daemon reads that file and never guesses a path. An archive without one is not an artifact.
