"""Schema/template parity for the DynamoDB bootstrap (no boto3 required).

These verify that the table definitions the container creates match what the
repos query and what the Lambda CloudFormation template defines. They import no
boto3, so they run in every environment.
"""
import os
import re

from shared.dynamo_bootstrap import _build_args
from shared.dynamo_schema import TABLES

_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "lambda", "template.yaml")


# ---------------------------------------------------------------------------
# _build_args
# ---------------------------------------------------------------------------

class TestBuildArgs:
    def test_uses_on_demand_billing(self):
        for spec in TABLES:
            assert _build_args(spec)["BillingMode"] == "PAY_PER_REQUEST"

    def test_projects_all_attributes_into_gsis(self):
        for spec in TABLES:
            for gsi in _build_args(spec).get("GlobalSecondaryIndexes", []):
                assert gsi["Projection"] == {"ProjectionType": "ALL"}

    def test_table_with_no_gsi_omits_index_key(self):
        tenants = next(s for s in TABLES if s["TableName"] == "reach-tenants")
        assert "GlobalSecondaryIndexes" not in _build_args(tenants)

    def test_attribute_definitions_cover_all_key_attributes(self):
        for spec in TABLES:
            defined = {a["AttributeName"] for a in spec["AttributeDefinitions"]}
            used = {k["AttributeName"] for k in spec["KeySchema"]}
            for gsi in spec.get("GlobalSecondaryIndexes", []):
                used |= {k["AttributeName"] for k in gsi["KeySchema"]}
            assert used == defined, f"{spec['TableName']}: attr defs {defined} != key attrs {used}"


# ---------------------------------------------------------------------------
# Schema parity with deploy/lambda/template.yaml
# ---------------------------------------------------------------------------

def _parse_template_tables():
    t = open(_TEMPLATE).read()
    tables = {}
    for m in re.finditer(r'^\s{2}\w+Table:\n(.*?)(?=^\s{2}\w+:|\Z)', t, re.S | re.M):
        block = m.group(0)
        name_m = re.search(r'TableName:\s*(\S+)', block)
        if not name_m:
            continue
        head = block.split('GlobalSecondaryIndexes')[0]
        pk = re.findall(r'AttributeName:\s*(\w+)\n\s*KeyType:\s*(\w+)', head)
        gsis = {}
        if 'GlobalSecondaryIndexes' in block:
            section = block.split('GlobalSecondaryIndexes')[1]
            for g in re.finditer(r'IndexName:\s*(\S+)\n(.*?)(?=- IndexName:|ProjectionType|\Z)', section, re.S):
                gsis[g.group(1)] = re.findall(r'AttributeName:\s*(\w+)\n\s*KeyType:\s*(\w+)', g.group(2))
        tables[name_m.group(1)] = {"pk": pk, "gsis": gsis}
    return tables


def _schema_tables():
    out = {}
    for spec in TABLES:
        pk = [(k["AttributeName"], k["KeyType"]) for k in spec["KeySchema"]]
        gsis = {
            g["IndexName"]: [(k["AttributeName"], k["KeyType"]) for k in g["KeySchema"]]
            for g in spec.get("GlobalSecondaryIndexes", [])
        }
        out[spec["TableName"]] = {"pk": pk, "gsis": gsis}
    return out


def test_dynamo_schema_matches_template():
    """dynamo_schema.py is the container-deployment mirror of the SAM template's
    DynamoDB tables. If they drift, FastAPI+dynamo would create tables whose
    indexes don't match what the repos query."""
    tmpl = _parse_template_tables()
    schema = _schema_tables()
    assert set(schema) == set(tmpl), (
        f"table set differs: only in schema={set(schema)-set(tmpl)}, "
        f"only in template={set(tmpl)-set(schema)}"
    )
    for name in schema:
        assert schema[name]["pk"] == tmpl[name]["pk"], f"{name}: primary key differs"
        assert schema[name]["gsis"] == tmpl[name]["gsis"], f"{name}: GSIs differ"
