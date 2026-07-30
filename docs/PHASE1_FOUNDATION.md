# Phase 1 platform foundation

This branch introduces the additive foundation for a tenant-safe Smart School SMS
platform. It preserves the existing `schools`, `users`, student, academic,
payment, and messaging records.

## Deployment order

1. Back up the production PostgreSQL database and verify that the backup can be
   restored.
2. Configure all required environment variables in Render. Never copy real
   secrets into source files.
3. Deploy the branch. Render runs `python manage.py migrate` before Gunicorn.
4. Confirm `/health` reports `status: ok`.
5. Test owner, school administrator, teacher, student, and parent access using a
   test school before enabling the release for all schools.

`python manage.py migrate` is versioned and idempotent. The Phase 1 migration
only creates tables, indexes, and nullable/defaulted columns. It does not drop,
rename, or truncate existing tables.

## Required production variables

- `SECRET_KEY`: at least 32 random characters.
- `DATABASE_URL`: the Render PostgreSQL connection string.
- `APP_URL`: the public HTTPS application URL.
- `SESSION_COOKIE_SECURE=1`
- `SESSION_COOKIE_SAMESITE=Lax`
- `PASSWORD_MIN_LENGTH=10` or greater.

Payment and email features additionally require:

- `PAYSTACK_PUBLIC_KEY`
- `PAYSTACK_SECRET_KEY`
- `PAYSTACK_CURRENCY=GHS`
- `STUDENT_REPORT_FEE=10.00`
- `RESEND_API_KEY`
- `EMAIL_FROM`: a sender on a domain verified in Resend.

For SMS, configure `SMS_ALLOWED_HOSTS` with a comma-separated allowlist and use
an HTTPS provider endpoint. A school cannot save an endpoint outside this list.

## Security behavior

- Every tenant record is queried with its `school_id`.
- Suspended or archived schools cannot sign in.
- Sessions carry a version number; password changes and “logout all devices”
  invalidate older sessions.
- Password-reset links are hashed in the database, expire, and are single-use.
- Reset requests are throttled and return the same response whether an account
  exists or not.
- Published marks are locked. An administrator correction requires a reason,
  creates an audit history row, reopens the mark as a draft, and requires
  republishing.
- CSV exports neutralize spreadsheet formula prefixes.

## Backup and rollback

Take a Render PostgreSQL backup immediately before deploying. Also record the
current Git commit and export persistent uploads. If rollback is required,
deploy the previous Git commit. The additive columns and tables may remain in
the database safely; do not delete them during an emergency rollback.

Two repository files need a separate security decision: `.env` and
`smart_schools_sms.db` are already tracked in Git even though ignore rules now
cover them. They should be untracked after a verified backup, and any historical
secret should be rotated. Rewriting Git history is intentionally not part of
this branch because it is destructive and requires explicit approval.
