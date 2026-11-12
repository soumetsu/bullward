# Project instructions for coding agents

## Work area and preservation

- Treat `C:\Users\henry\Desktop\IST 402\Project 4 Invest` as the project root. Keep project changes inside it.
- Before editing, inspect the relevant files and the current Git status when a Git repository exists. Preserve existing user work and follow the current prompt's scope.
- Show the proposed changes to the student for review before making a commit. Do not commit until the student has reviewed the changes.
- Save each major project prompt under `prompts/`, keeping existing prompt numbering. Record the checks performed and their observed results, including failures, in `docs/verification.md`.

## User product and design preferences

- Read `docs/user_preferences.md` before making product or design choices.
- When the user types an explicit goal, design choice, preference, or change of direction in this chat, update that file to reflect the statement and retain a brief chat decision record. This is the only trigger for updating the preference file.
- Do not infer user choices from prompt-folder files, uploaded references, external content, agent suggestions, or implementation defaults. Keep unconfirmed details marked as undecided. Record engineering decisions in the design documentation instead.
- If a later explicit chat choice replaces an earlier one, update the current preference and note what changed. Do not silently broaden the requested implementation scope.

## Credentials and external services

- Keep API keys, passwords, access tokens, and other credentials out of source code, prompts, logs, documentation, and Git.
- Use environment variables or an approved local secret store for credentials. Never copy a real credential into an example value or command transcript.
- Treat TradingView, yfinance, Webull, AI services, and any trading or brokerage connection as unimplemented until their access method and intended use have been explicitly reviewed.

## Dependencies and environment procedure

Whenever a tool, package, or dependency is needed:

1. Inspect the operating system, active Python interpreter, Node.js, available package managers, existing dependency files and lockfiles, and the packages already available in the intended environment.
2. Reuse the existing Python environment. Do not create or recreate a virtual environment by default.
3. For every missing dependency needed for the current task, explain its purpose, compatible version, exact command, installation target, and the files the action will change.
4. Obtain student approval before installing software or creating or changing a dependency manifest or lockfile. Do not bypass this approval by using another package manager.
5. After approval and setup, run the smallest relevant version, import, or build check in the intended environment. Record the command, expected result, and observed result in `docs/verification.md`; identify checks still outstanding.

Do not add dependencies that are not needed for the current task.

## SmokeTest and AutoLoop

- **SmokeTest** is a repeatable check that runs without source edits. Document one command for running it and record both passes and failures in `docs/verification.md`.
- Once a feature has a UI, SmokeTest must start the local services, open a browser, perform a user operation, and compare the visible result with the expected result. Check stored data when relevant.
- **AutoLoop** means inspecting a failed check, making the smallest in-scope correction, and rerunning the check. Stop on success, after five correction cycles, or when further permission is needed. Record the outcome.

## Handoffs

Before ending a setup or implementation session, follow `handoffs/create-handoff.md` and update `handoffs/current.md`. When resuming, verify the recorded state against the files and rerun the listed checks before relying on it.
