"""
Parity guard: the 404 page embedded in the CloudFront UI function must stay identical to
shared/error_page.py.

The Lambda deployment serves the console UI from S3 via CloudFront. Stray paths *strictly* under
/ui/ (e.g. /ui/settings) go to the S3 origin and can't reach the API's catch-all 404 handler, so the
CloudFront function (UiPathRewriteFunction in deploy/lambda/template.yaml) returns the 404 itself -
which means it has to carry a copy of the page. This test asserts that embedded copy is byte-for-byte
NOT_FOUND_HTML, so it can never silently drift from the FastAPI adapter / the Lambda catch-all
(handlers/not_found.py), which both serve the same shared page.
"""
import os
import re

from shared.error_page import NOT_FOUND_HTML

_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "lambda", "template.yaml")


def _ui_function_code() -> str:
    import yaml

    class _L(yaml.SafeLoader):
        pass

    # Tolerate CloudFormation intrinsic tags (!Ref/!GetAtt/!Sub ...) so we can read the template.
    _L.add_multi_constructor("!", lambda loader, suffix, node: None)
    with open(_TEMPLATE) as f:
        doc = yaml.load(f, Loader=_L)
    return doc["Resources"]["UiPathRewriteFunction"]["Properties"]["FunctionCode"]


def test_embedded_404_html_matches_shared_page():
    code = _ui_function_code()
    m = re.search(r"var body = \[(.*?)\]\.join\('\\n'\)", code, re.S)
    assert m, "could not find the `var body = [...].join('\\n')` block in UiPathRewriteFunction"
    # Each array element is a single-quoted line; NOT_FOUND_HTML contains no single quotes, so a
    # simple quoted-run match is exact. Join mirrors the function's .join('\n').
    lines = re.findall(r"'([^']*)'", m.group(1))
    embedded = "\n".join(lines)
    assert embedded == NOT_FOUND_HTML, (
        "the 404 page embedded in the CloudFront function has drifted from "
        "shared/error_page.py NOT_FOUND_HTML - regenerate the function's body array from it"
    )


def test_embedded_json_404_matches_catch_all():
    import json
    code = _ui_function_code()
    # Must match handlers/not_found.py's non-HTML branch: json.dumps({"error": "not found"}).
    assert json.dumps({"error": "not found"}) in code, (
        "the JSON 404 body in the CloudFront function must match handlers/not_found.py"
    )
