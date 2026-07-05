"""The per-component contract blocks in components.json (PROTOCOL.md
§ component contracts).

The contract is the machine-readable statement of what an SDK may set/send
and what a panel emits — the layer above the wire protocol that used to be
folklore (readable only in the Python source and the template JSX). These
tests keep it honest three ways: every template must carry one, every data
field the template defaults or its JSX reads must be declared, and the
committed asset can't drift from the declarations (via the existing
freshness test in test_component_templates.py, which compares the whole
build output).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from danvas.source import _templates

# props.* names the JSX may read that are register-prop plumbing, not data
# fields: the data blob itself, sizing, and React-host machinery.
_INFRA_PROPS = {"data", "w", "h", "libs", "css", "wasm", "autoH", "autoW",
                "source", "permissions", "themed"}

_REQUIRED_KEYS = {"data", "updates", "events", "encoded", "geometry"}


def test_every_template_declares_a_contract():
    for kind, tpl in _templates().items():
        contract = tpl.get("contract")
        assert isinstance(contract, dict), f"{kind}: no contract block"
        missing = _REQUIRED_KEYS - set(contract)
        assert not missing, f"{kind}: contract missing {sorted(missing)}"
        geo = contract["geometry"]
        assert isinstance(geo.get("w"), (int, float)), f"{kind}: geometry.w"
        assert isinstance(geo.get("h"), (int, float)), f"{kind}: geometry.h"


def test_template_data_defaults_are_declared():
    # Every default the template ships must be a declared data field — an SDK
    # reading the contract sees the full authorable surface.
    for kind, tpl in _templates().items():
        declared = set(tpl["contract"]["data"])
        undeclared = set(tpl["data"]) - declared
        assert not undeclared, (
            f"{kind}: template data defaults {sorted(undeclared)} are not "
            "declared in the component's CONTRACT")


def test_jsx_reads_are_declared():
    # Every `props.<field>` the template JSX reads must be a declared data
    # field, a declared register prop, or React-host plumbing. Catches a JSX
    # gaining a field the contract forgot (the table's `editable` was exactly
    # this kind of hole before contracts existed).
    for kind, tpl in _templates().items():
        src = tpl["props"].get("source")
        if not isinstance(src, str):
            continue
        contract = tpl["contract"]
        allowed = (set(contract["data"]) | set(contract.get("props", {}))
                   | _INFRA_PROPS)
        reads = set(re.findall(r"props\.(\w+)", src))
        unknown = reads - allowed
        assert not unknown, (
            f"{kind}: JSX reads props.{sorted(unknown)} not declared in the "
            "component's CONTRACT")


def test_encoded_fields_are_a_shrinking_list():
    # `encoded` marks legacy string-double-encoded data fields. New ones are
    # a regression: template JSX must parse tolerantly (string OR plain JSON)
    # instead — see the inspector's asJson. Freeze the current set at empty.
    for kind, tpl in _templates().items():
        assert tpl["contract"]["encoded"] == [], (
            f"{kind}: new string-encoded fields are not allowed — make the "
            "template JSX parse tolerantly instead")
