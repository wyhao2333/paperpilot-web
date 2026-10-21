# PaperPilot Web

This directory is an independent, local-first web application. Keep changes inside this directory. Do not modify the parent Zotero extension or ANNA_APP.

- Stack: Python 3.11+, FastAPI, SQLite, pdfplumber; Vue 3 + TypeScript + Vite + PDF.js.
- Support Windows local testing and Ubuntu 24.04 / Docker. Avoid hard-coded Windows paths.
- User scope: a few invited users, private documents per account, translation first.
- Persist translation jobs, results, and reading progress. Preserve failed paragraphs and parsing warnings.
- Never silently switch translation providers or simulate a successful translation.
- Never commit PDFs uploaded by users, database files, credentials, encryption keys, .env, or runtime data.
- Run backend tests and the frontend production build for substantive changes. Report the boundary between mock API checks and real provider checks.
- The user will test localhost first. Create a new public GitHub repository with gh only after the user confirms that local testing passed. Never push this parent repository or its history into the new repository.
- Subagents, if explicitly authorized: gpt-5.6-sol by default, gpt-6-astra for harder tasks; never select another model.
