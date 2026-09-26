# Instructions for AI coding agents

Read `PROJECT_MEMORY.md` first: it explains the whole GleasonAI project (architecture, technologies and
versions, folder layout, how it was built, how to run and test it, the rules that must not be broken, and
known gaps). Key rules: never change the model/preprocessing maths without re-running the thesis checks;
keep the API torch-free; audit every state-changing action; never commit `.env`, `models/`, `storage/`,
`backups/`; run `make test` and `make lint` before committing.
