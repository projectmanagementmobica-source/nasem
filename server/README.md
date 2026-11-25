# NASEM server (Vercel)

The owner's server for NASEM: activation approvals (/admin), the AI gateway, reminders, family and encrypted backups.

- Runs as one Vercel Function in Frankfurt (`fra1`), next to the Neon database.
- Database migrations run by themselves on start.
- Reminders: for each due second the server books one call to `/v1/internal/tick` with Upstash QStash
  (`QSTASH_TOKEN`), so the database is never polled and can sleep. An hourly QStash schedule (created
  automatically) is the safety net: missed sends, family missed-dose checks, booking the next 24 hours,
  and a daily clean-up at 03:00 UTC.
- Secrets live in Vercel → Settings → Environment Variables, never in these files.
