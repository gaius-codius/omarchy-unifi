"""The UniFi status helper's own package."""

# This file's presence is the point of it, not its contents.
#
# Without an __init__.py, `unifi` would be a PEP 420 NAMESPACE package, and
# namespace packages MERGE across every sys.path entry rather than stopping at
# the first match. `unifi` is a real name on PyPI, so a directory of that name
# anywhere later on the path — the system site-packages, which `-s` does not
# remove — would contribute modules into this package. `from unifi import
# tlsctx` could then resolve to somebody else's file, in the process that holds
# the API key.
#
# A regular package pins __path__ to exactly this directory. tests/test_unifi_status.py
# asserts that, because the failure is silent: the wrong module imports cleanly
# and does whatever it likes.
