"""Hand-built log lines, including the ones that break parsers. Shared across tests.
Deliberately not: generated or sampled data - a fixture you can't read is a fixture
you can't reason about when it fails.
"""

from __future__ import annotations

from typing import Final

SYSLOG: Final[str] = (
    "Aug 22 14:03:11 web-01 sshd[4412]: Accepted publickey for aroni from 10.0.0.7 port 51234"
)

APACHE_CLF: Final[str] = (
    '10.0.0.7 - aroni [22/Aug/2026:14:03:11 +0000] "GET /api/v1/items?id=7 HTTP/1.1" 200 4213'
)

LOGFMT: Final[str] = 'level=INFO msg="cache miss" key=user:42 duration_ms=13.5'

# Embedded JSON with escaped quotes inside a JSON line: the classic double-unescape bug.
JSON_LINE_ESCAPED_QUOTES: Final[str] = (
    '{"timestamp":"2026-08-22T14:03:11Z","level":"ERROR",'
    '"message":"upstream said {\\"error\\": \\"bad \\\\\\"gateway\\\\\\"\\"}"}'
)

# Non-ASCII in every position a parser might assume is ASCII.
UNICODE_LINE: Final[str] = (
    'level=WARN msg="disque plein — sauvegarde échouée ✗" user=jörg host=café-01'
)

# Cut mid-token, as happens when a writer is killed or a buffer wraps.
TRUNCATED_LINE: Final[str] = '{"timestamp":"2026-08-22T14:03:11Z","level":"ERR'

# Six lines: header plus five continuations. multiline.py must make this one record.
JAVA_STACK_TRACE_LINES: Final[tuple[str, ...]] = (
    "2026-08-22 14:03:11 ERROR [http-nio-8080-exec-3] c.p.ItemService - lookup failed",
    "java.lang.IllegalStateException: connection pool exhausted",
    "\tat com.prologis.ItemService.lookup(ItemService.java:88)",
    "\tat com.prologis.ItemController.get(ItemController.java:41)",
    "\tat java.base/java.lang.Thread.run(Thread.java:840)",
    "Caused by: java.sql.SQLTimeoutException: timeout acquiring connection",
)

# Correctly typed, correctly shaped, and wrong: the fields are swapped. Schema
# validation cannot catch this, which is exactly why exact-match scoring exists.
SEMANTIC_SWAP_GOLD_FIELDS: Final[dict[str, str]] = {"level": "INFO", "method": "GET"}
SEMANTIC_SWAP_PREDICTED_FIELDS: Final[dict[str, str]] = {"level": "GET", "method": "INFO"}


# --- Valid field sets, one per shape, satisfying configs/schema.json ------------------

VALID_FIELDS: Final[dict[str, dict[str, str]]] = {
    "syslog": {
        "timestamp": "Aug 22 14:03:11",
        "host": "web-01",
        "process": "sshd",
        "pid": "4412",
        "message": "Accepted publickey for aroni",
    },
    "json_lines": {
        "timestamp": "2026-08-22T14:03:11Z",
        "level": "ERROR",
        "message": "upstream unavailable",
    },
    "logfmt": {"level": "INFO", "message": "cache miss", "duration_ms": "13.5"},
    "apache_clf": {
        "client_ip": "10.0.0.7",
        "timestamp": "22/Aug/2026:14:03:11 +0000",
        "method": "GET",
        "path": "/api/v1/items",
        "protocol": "HTTP/1.1",
        "status": "200",
        "bytes": "4213",
    },
    "multiline_trace": {
        "exception": "java.lang.IllegalStateException",
        "stack": "at com.prologis.ItemService.lookup(ItemService.java:88)",
    },
}

# Swapped host and process. Both are free-form non-empty strings, so the schema cannot
# tell them apart - this is the true schema-valid-but-wrong case for THIS schema.
SYSLOG_SWAPPED_FIELDS: Final[dict[str, str]] = {
    **VALID_FIELDS["syslog"],
    "host": "sshd",
    "process": "web-01",
}
