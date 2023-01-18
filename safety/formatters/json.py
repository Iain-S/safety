import logging

import json as json_parser
from collections import defaultdict
from typing import Iterable

from requests.models import PreparedRequest

from safety.formatter import FormatterAPI
from safety.output_utils import get_report_brief_info
from safety.safety import find_vulnerabilities_fixed
from safety.util import get_basic_announcements, SafetyContext

LOG = logging.getLogger(__name__)


class JsonReport(FormatterAPI):
    """Json report, for when the output is input for something else"""

    def render_vulnerabilities(self, announcements, vulnerabilities, remediations, full, packages, fixes=()):
        remediations_recommended = len(remediations.keys())
        LOG.debug('Rendering %s vulnerabilities, %s remediations with full_report: %s', len(vulnerabilities),
                  remediations_recommended, full)
        vulns_ignored = [vuln.to_dict() for vuln in vulnerabilities if vuln.ignored]
        vulns = [vuln.to_dict() for vuln in vulnerabilities if not vuln.ignored]

        report = get_report_brief_info(as_dict=True, report_type=1, vulnerabilities_found=len(vulns),
                                       vulnerabilities_ignored=len(vulns_ignored),
                                       remediations_recommended=remediations_recommended)

        remed = {}
        for k, v in remediations.items():
            if k not in remed:
                remed[k] = {}

            closest = v.get('closest_secure_version', {})
            upgrade = closest.get('major', None)
            downgrade = closest.get('minor', None)

            recommended_version = None

            if upgrade:
                recommended_version = str(upgrade)
            elif downgrade:
                recommended_version = str(downgrade)

            remed[k]['current_version'] = v.get('version', None)
            remed[k]['vulnerabilities_found'] = v.get('vulns_found', 0)
            remed[k]['recommended_version'] = recommended_version
            remed[k]['other_recommended_versions'] = [other_v for other_v in v.get('secure_versions', []) if
                                                      other_v != recommended_version]
            remed[k]['more_info_url'] = v.get('more_info_url', '')

            # Use Request's PreparedRequest to handle parsing, joining etc the URL since we're adding query
            # parameters and don't know what the server might send down.
            if remed[k]['more_info_url']:
                req = PreparedRequest()
                req.prepare_url(remed[k]['more_info_url'], {'from': remed[k]['current_version'], 'to': recommended_version})
                remed[k]['more_info_url'] = req.url

        template = {
            "report_meta": report,
            "scanned_packages": {p.name: p.to_dict(short_version=True) for p in packages},
            "affected_packages": {v.pkg.name: v.pkg.to_dict() for v in vulnerabilities},
            "announcements": [{'type': item.get('type'), 'message': item.get('message')} for item in
                              get_basic_announcements(announcements)],
            "vulnerabilities": vulns,
            "ignored_vulnerabilities": vulns_ignored,
            "remediations": remed
        }

        template = self.__render_fixes(template, fixes)

        return json_parser.dumps(template, indent=4)

    def render_licenses(self, announcements, licenses):
        unique_license_types = set([lic['license'] for lic in licenses])
        report = get_report_brief_info(as_dict=True, report_type=2, licenses_found=len(unique_license_types))

        template = {
            "report_meta": report,
            "announcements": get_basic_announcements(announcements),
            "licenses": licenses,
        }

        return json_parser.dumps(template, indent=4)

    def render_announcements(self, announcements):
        return json_parser.dumps({"announcements": get_basic_announcements(announcements)}, indent=4)

    def __render_fixes(self, scan_template, fixes: Iterable):

        applied = defaultdict(dict)
        skipped = defaultdict(dict)

        fixes_applied = []

        for fix in fixes:
            if fix.status == 'APPLIED':
                applied[fix.applied_at][fix.package] = {
                    "previous_version": str(fix.previous_version),
                    "updated_version": str(fix.updated_version),
                    "update_type": str(fix.update_type),
                    "fix_type": fix.fix_type
                }
                fixes_applied.append(fix)
            else:
                skipped[fix.applied_at][fix.package] = {
                    "scanned_version": str(fix.previous_version) if fix.previous_version else None,
                    "skipped_reason": fix.status
                }

        vulnerabilities = scan_template.get("vulnerabilities", {})
        remediation_mode = 'NON_INTERACTIVE'

        if SafetyContext().params.get('prompt_mode', False):
            remediation_mode = 'INTERACTIVE'

        scan_template['report_meta'].update(
            {'remediations_attempted': len(fixes),
             'remediations_completed': len(applied),
             'remediation_mode': remediation_mode}
        )

        scan_template['remediations_results'] = {
            "vulnerabilities_fixed": find_vulnerabilities_fixed(vulnerabilities, fixes_applied),
            "remediations_applied": applied,
            "remediations_skipped": skipped
        }

        return scan_template
