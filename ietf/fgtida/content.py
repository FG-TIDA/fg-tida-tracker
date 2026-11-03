"""Stable FG-TIDA text and public links, with no database access."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicContent:
    home_title: str = 'Datatracker'
    home_intro: str = ('The FG-TIDA Datatracker supports the day-to-day work of the Focus Group on Trust and Identity for Humans and Agentic AI.\n'
        'It contains documents, working groups, meetings, agendas, minutes, presentations, and recordings.')
    official_url: str = 'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/default.aspx'
    registration_url: str = 'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/default.aspx'
    community_title: str = 'FG-TIDA Community'
    community_description: str = 'Explore community news, activities and ways to participate in the work on trust and identity for humans and agentic AI.'
    community_url: str = 'https://tida.community/'
    secretariat_email: str = 'tsbfgtida@itu.int'
    bug_report_url: str = 'https://github.com/FG-TIDA/fg-tida-tracker/issues'


CONTENT = PublicContent()


def get_content(request=None):
    return CONTENT


def public_content(request):
    return {'platform_content': get_content(request)}
