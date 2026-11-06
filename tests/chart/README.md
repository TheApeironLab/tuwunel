# Deployment regression checks

Run `python3 -m unittest discover -s tests/chart -p 'test_*.py'` with Helm 3.19 on PATH
(or `HELM=/explicit/path/helm`) and dependencies from `release/requirements.txt`.
Tests render namespace-local prerequisites, 443/non-default-port CIDR rules, fixed image
role overrides, OIDC/URL guards, optional directory-based admin synchronization, and
retained topology. Packaging checks reject dirty formal sources and changed image bytes.
Initializer tests cover room reuse/encryption rejection and directory-denied no-write behavior.
These are local regressions. They do not replace a new candidate's real Vasi installation,
SSO/message/media/alert checks or optional IAM directory/backup/restore acceptance.
