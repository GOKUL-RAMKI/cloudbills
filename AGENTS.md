# Working agreement

## Commits

- Commit after each completed and verified slice of work, not in one giant commit.
- Stage only the files that belong to that slice. Never stage unrelated edits.
- Commit messages must be plain, imperative and descriptive (for example
  `Add PostgreSQL schema, stored programs and seed data`).
- Never mention AI tools, models, assistants or automated generation in commit
  messages, branches, tags or pull requests.
- Never add `Co-Authored-By` trailers, generated-with footers or any AI
  attribution to commits. The AI is not a project contributor.
- Do not rewrite published history, force-push or amend unless explicitly asked.

## Local setup

Database (PostgreSQL 16 in Docker):

```powershell
docker start cloudbills-db
```

Initialise schema and demonstration data:

```powershell
python backend/scripts/init_db.py --reset
```

Run the application:

```powershell
python run.py
```

Run tests:

```powershell
python -m pytest backend/tests -q
```
