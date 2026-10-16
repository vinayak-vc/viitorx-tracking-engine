"""`fetch-models`: put the registry's model files where the engine looks for them, and refuse any file that is not byte-for-byte the one it was measured with.

Only the https URLs in registry.py are ever fetched. A download goes to a `.part` file, is checked for the exact size (a longer stream is not this file
and must not fill a disk) and the SHA-256, and only then replaces the target. Nothing is downloaded when the file is already right, and `--check` downloads
nothing at all. The body model has no URL: it is reported as "provide it yourself".
"""
import os
import urllib.request

from . import registry as R

#: Seconds without a byte before a download is abandoned.
TIMEOUT_SECONDS = 30


class FetchError(RuntimeError):
    pass


def download(spec, dest, opener=urllib.request.urlopen, timeout=TIMEOUT_SECONDS):
    """Fetch `spec.url` to `dest` through a temp file; raise FetchError (leaving nothing behind) when it is not exactly the registry's file."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    got = 0
    try:
        with opener(spec.url, timeout=timeout) as r, open(part, "wb") as out:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > spec.size:
                    raise FetchError("the download of %s is longer than the expected %d bytes" % (spec.name, spec.size))
                out.write(chunk)
        if got != spec.size:
            raise FetchError("the download of %s is %d bytes, expected %d" % (spec.name, got, spec.size))
        if R.sha256_of(part) != spec.sha256:
            raise FetchError("the download of %s does not match its SHA-256" % spec.name)
        os.replace(part, dest)
    except FetchError:
        if os.path.exists(part):
            os.remove(part)
        raise
    except Exception as e:
        if os.path.exists(part):
            os.remove(part)
        raise FetchError("could not download %s: %s" % (spec.name, e))


def run(directory, groups, check_only, out, fetcher=download):
    """Make the folder right for these groups (None = every group with a URL). Returns 0 when every requested file is right, 1 otherwise.
    `out` is a print-like callable; `fetcher` is injectable for tests."""
    wanted = [g for g in R.GROUPS if groups is None or g in groups]
    problems = 0
    for group in wanted:
        for spec in R.specs_for(group):
            path = os.path.join(directory, spec.name)
            state = R.inspect(path, spec)
            if state == R.OK:
                out("OK       %-40s %s" % (spec.name, group))
                continue
            if state == R.CUSTOM:
                out("CUSTOM   %-40s %s (not the file this engine was measured with; left alone)" % (spec.name, group))
                continue
            if spec.url is None:
                tag = "MISSING " if (spec.required or groups is not None) else "absent  "
                out("%s %-40s %s (no download: place the file in %s)" % (tag, spec.name, group, directory))
                if groups is not None and group in groups and group == "body":
                    problems += 1
                continue
            if check_only:
                out("MISSING  %-40s %s" % (spec.name, group))
                problems += 1 if spec.required else 0
                continue
            out("fetching %s from %s" % (spec.name, spec.url))
            try:
                fetcher(spec, path)
            except FetchError as e:
                out("FAILED   %-40s %s" % (spec.name, e))
                problems += 1
                continue
            out("OK       %-40s %s (%d bytes, sha256 matches)" % (spec.name, group, spec.size))
    return 1 if problems else 0
