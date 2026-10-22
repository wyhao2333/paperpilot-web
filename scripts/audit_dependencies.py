"""Query OSV using only installed public package names and versions."""
import importlib.metadata
import json
import sys
import urllib.request


def main():
    packages = sorted((item.metadata["Name"], item.version) for item in importlib.metadata.distributions())
    data = {"queries": [{"package": {"ecosystem": "PyPI", "name": name}, "version": version} for name, version in packages]}
    request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
    if len(result.get("results", [])) != len(packages):
        raise ValueError("Incomplete dependency audit response")
    findings = []
    for (name, version), row in zip(packages, result["results"]):
        ids = sorted({item["id"] for item in row.get("vulns", [])})
        if ids:
            findings.append({"package": name, "version": version, "advisories": ids})
    print(json.dumps({"packages_checked": len(packages), "findings": findings}, indent=2))
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
