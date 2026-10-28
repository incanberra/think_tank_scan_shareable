"""Read IISS's server-supplied React props without executing JavaScript."""
import json
import re
from html import escape
from datetime import datetime
from bs4 import BeautifulSoup


def render_public_components(html):
    fragments = []
    article = []
    for match in re.finditer(r'React.createElement\(Components\.(\w+),\s*', html):
        try:
            data, _ = json.JSONDecoder().raw_decode(html[match.end():])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        kind = match[1]
        if kind == 'Filter':
            for rows in data.values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict) or not row.get('Link') or not row.get('Heading'):
                        continue
                    fragments.append('<div><a href="' + escape(row['Link'], quote=True) + '">' + escape(row['Heading']) + '</a><time>' + escape(row.get('Date') or '') + '</time></div>')
        elif kind == 'Intro':
            article.append('<h1>' + escape(data.get('Title') or '') + '</h1>' + (data.get('Intro') or ''))
        elif kind == 'ArticleNav':
            date = (data.get('Current') or {}).get('Date') or ''
            try:
                iso = datetime.strptime(re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date), '%d %B %Y').date().isoformat()
                article.append('<time datetime="' + iso + '">' + escape(date) + '</time>')
            except ValueError:
                pass
        elif kind == 'Reading':
            body = BeautifulSoup(data.get('Html') or '', 'html.parser')
            for div in body.find_all('div'):
                if not div.find(['div', 'p']):
                    div.name = 'p'
            article.append(str(body))
    if article:
        fragments.append('<article>' + ''.join(article) + '</article>')
    return html.replace('</body>', ''.join(fragments) + '</body>')
