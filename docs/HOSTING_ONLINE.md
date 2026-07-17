# Hosting Online

Use this when schools should access the system from anywhere on the internet.

## Simple Render Deployment

1. Push the project to GitHub.
2. Create a new Render web service from the GitHub repository.
3. Render can use `render.yaml`, or set these manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn run:app`
4. Add environment variables:
   - `SECRET_KEY`
   - `FLASK_DEBUG=0`
   - `DATABASE_URL`
   - `SCHOOL_UPLOAD_LIMIT_MB=5`
   - `SESSION_COOKIE_SECURE=1`
5. Deploy and open the public URL.

## PostgreSQL Database

For real schools, use PostgreSQL instead of SQLite.

1. Create a PostgreSQL database with your hosting provider.
2. Copy the connection string.
3. Add it as `DATABASE_URL`.
4. The app accepts both `postgres://...` and `postgresql://...` URLs.

Example:

```env
DATABASE_URL=postgresql://username:password@host:5432/database_name
```

## Important Production Notes

- Change the default system admin password before real use.
- Use PostgreSQL for many schools and users.
- Use persistent file storage for school crests and uploads.
- Keep HTTPS enabled.
- Back up the database and uploads regularly.

## Nalo SMS Setup

To send bulk SMS from the Communication module, add these environment variables in Render:

```env
SMS_API_URL=https://sms.nalosolutions.com/smsbackend/Resl_Nalo/send-message/
NALO_SMS_USERNAME=your_nalo_username
NALO_SMS_PASSWORD=your_nalo_password
NALO_SMS_SENDER_ID=YOUR_SENDER_ID
```

Use the credentials and approved sender ID supplied by Nalo Solutions. Keep these values private and never place them in source code.
