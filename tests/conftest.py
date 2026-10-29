import os
import tempfile

# Keep tests away from the real ~/.osai.
os.environ.setdefault("OSAI_HOME", tempfile.mkdtemp(prefix="osai-test-"))
os.environ.setdefault("OSAI_ALLOWED_HOSTS", "testserver")  # Starlette TestClient's Host header
os.environ.setdefault("OSAI_SYSTEM_CONFIG", tempfile.mkdtemp(prefix="osai-etc-"))  # ignore a real /etc/osai
