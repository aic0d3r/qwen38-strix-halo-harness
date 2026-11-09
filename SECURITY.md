# Security

Report a vulnerability privately through the repo's Security tab ("Report a vulnerability"). Please don't open a public
issue with exploit details.

In scope: the setup scripts, the extensions (including the `taint-gate` prompt-injection gate and `auto-guard`) and
anything that handles credentials or runs commands. Out of scope: the engines (Strata, llama.cpp, vLLM, Ollama,
LM Studio, halogen, gufo) and pi itself. Report those upstream.

`auto-guard` is a fail-open tripwire, not a security boundary (see the README).
