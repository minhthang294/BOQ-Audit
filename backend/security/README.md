# Worker seccomp profile

`codex-worker-seccomp.json` is copied from OpenAI's Codex Security repository at commit `10e7a22461329083414713c379e97a0c352647fe`.
SHA-256: `4d9767cdcdc79338c0ce172baab5042d1c5a7d847a0d73a10e308f54c44a286d`.
Source: https://github.com/openai/codex-security/blob/10e7a22461329083414713c379e97a0c352647fe/docker/codex-security-seccomp.json

The worker uses this policy with UID/GID 10001, all capabilities dropped, and `no-new-privileges`; do not replace it with `seccomp=unconfined` or privileged mode. Re-pin and rerun the container namespace and Codex filesystem canaries before changing this file.
