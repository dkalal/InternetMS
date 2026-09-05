from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from customers.domain_audit import build_domain_audit


class Command(BaseCommand):
    help = "Read-only audit of customer/site/service/subscription and financial-document ownership."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--format", choices=("text", "json"), default="text")
        parser.add_argument("--output", default="", help="Optional report file. Existing files are not overwritten.")
        parser.add_argument("--sample-limit", type=int, default=100)
        parser.add_argument("--fail-on-ambiguity", action="store_true")

    def handle(self, *args, **options):
        if options["sample_limit"] < 0:
            raise CommandError("--sample-limit must be zero or greater.")
        report = build_domain_audit(
            tenant_id=options["tenant_id"],
            sample_limit=options["sample_limit"],
        )
        rendered = self._render_json(report) if options["format"] == "json" else self._render_text(report)
        output = options["output"].strip()
        if output:
            path = Path(output).resolve()
            if path.exists():
                raise CommandError(f"Refusing to overwrite existing report: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + "\n", encoding="utf-8")
            self.stdout.write(str(path))
        else:
            self.stdout.write(rendered)
        if options["fail_on_ambiguity"] and report["summary"]["blocking_ambiguities"]:
            raise CommandError("Blocking ownership ambiguities were detected; deterministic backfill must not proceed.")

    @staticmethod
    def _render_json(report):
        return json.dumps(report, indent=2, sort_keys=True)

    @staticmethod
    def _render_text(report):
        lines = ["JBMS domain ownership audit (read only)", ""]
        lines.append("Counts:")
        for key, value in report["counts"].items():
            lines.append(f"  {key}: {value}")
        lines.extend([
            "",
            f"Blocking ambiguities: {report['summary']['blocking_ambiguities']}",
            f"Review findings: {report['summary']['review_findings']}",
            f"Safe for deterministic backfill: {'yes' if report['summary']['safe_to_begin_deterministic_backfill'] else 'NO'}",
            "",
            "Findings:",
        ])
        if not report["findings"]:
            lines.append("  none")
        for code, finding in report["findings"].items():
            lines.append(f"  [{finding['severity'].upper()}] {code}: {finding['count']}")
            lines.append(f"    {finding['description']}")
            for record in finding["records"]:
                lines.append(f"    - {json.dumps(record, sort_keys=True)}")
        return "\n".join(lines)
