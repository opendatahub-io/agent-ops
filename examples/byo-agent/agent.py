"""Stand-in for your agent: one allowed HTTPS call, one denied one."""
import urllib.error
import urllib.request


def call(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:  # denied egress surfaces as a proxy error
        return f"blocked ({type(e).__name__})"


print("allowed  https://api.github.com/zen  ->", call("https://api.github.com/zen"))
print("denied   https://example.com/        ->", call("https://example.com/"))
