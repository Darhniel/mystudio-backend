"""Management command to expire pending bookings past their payment hold expiry.

Usage:
    python manage.py expire_pending_bookings
    python manage.py expire_pending_bookings --batch-size=50

This command is designed to be scheduled to run every minute in production.
It is safe to execute repeatedly and will only expire bookings that have
passed their payment hold expiry time.

Scheduling in production:

Option 1: Cron (Linux/macOS)
    crontab -e
    * * * * * /path/to/venv/bin/python /path/to/project/manage.py expire_pending_bookings >> /var/log/mystudio/expire_bookings.log 2>&1

Option 2: GitHub Actions (if using GitHub-hosted infrastructure)
    .github/workflows/expire-bookings.yml:
    name: Expire Pending Bookings
    on:
      schedule:
        - cron: '* * * * *'  # Every minute
    jobs:
      expire:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v3
          - name: Set up Python
            uses: actions/setup-python@v4
            with:
              python-version: '3.15'
          - name: Install dependencies
            run: pip install -r requirements.txt
          - name: Expire bookings
            run: python manage.py expire_pending_bookings
            env:
              DATABASE_URL: ${{ secrets.DATABASE_URL }}
              SECRET_KEY: ${{ secrets.SECRET_KEY }}

Option 3: Supabase Cron (if using Supabase)
    -- Enable pg_cron extension in Supabase dashboard
    -- Then create a scheduled function or use an external scheduler

Option 4: Docker/Kubernetes CronJob
    apiVersion: batch/v1
    kind: CronJob
    metadata:
      name: expire-bookings
    spec:
      schedule: "* * * * *"
      jobTemplate:
        spec:
          template:
            spec:
              containers:
              - name: expire-bookings
                image: your-image:tag
                command: ["python", "manage.py", "expire_pending_bookings"]
              restartPolicy: OnFailure
"""

from django.core.management.base import BaseCommand

from apps.bookings.services import expire_pending_bookings_batch


class Command(BaseCommand):
    help = "Expire pending bookings that have passed their payment hold expiry time."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of bookings to process in each batch (default: 100).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]

        self.stdout.write(f"Starting expiration sweep with batch size {batch_size}...")

        try:
            count = expire_pending_bookings_batch(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(f"Successfully expired {count} pending bookings.")
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error during expiration sweep: {e}"))
            raise
