"""Подтверждённые связи книг с циклами из структурированных данных Викиданных."""

import json
import re
from collections import defaultdict
from urllib.parse import urlencode

from . import core, library

API = "https://query.wikidata.org/sparql"


def find_cycles(author: str) -> dict[str, tuple[str, int]]:
    literal = json.dumps(author, ensure_ascii=False)
    query = f"""
SELECT DISTINCT ?author ?book ?title ?cycle ?cycleLabel ?position WHERE {{
  VALUES ?name {{ {literal}@ru {literal}@en }}
  ?author (rdfs:label|skos:altLabel) ?name; wdt:P31 wd:Q5.
  OPTIONAL {{
    ?book wdt:P50 ?author; p:P179 ?membership;
          (rdfs:label|skos:altLabel) ?title.
    FILTER(LANG(?title) IN ("ru", "en"))
    ?membership ps:P179 ?cycle.
    FILTER NOT EXISTS {{ ?membership wikibase:rank wikibase:DeprecatedRank }}
    OPTIONAL {{ ?membership pq:P1545 ?position. }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
}}
LIMIT 5001
"""
    reply = json.loads(core.http_get(API + "?" + urlencode({"query": query}),
                                     timeout=25, accept="application/sparql-results+json"))
    rows = reply["results"]["bindings"]
    if len(rows) > 5000 or len({row["author"]["value"] for row in rows}) != 1:
        return {}
    titles = defaultdict(set)
    positions = defaultdict(set)
    names = {}
    for row in rows:
        if "title" not in row:
            continue
        title = library.norm_title(row["title"]["value"])
        book, cycle = row["book"]["value"], row["cycle"]["value"]
        titles[title].add((book, cycle))
        names[cycle] = row.get("cycleLabel", {}).get("value", "")
        position = row.get("position", {}).get("value", "").strip()
        if re.fullmatch(r"[0-9]{1,9}", position) and int(position) > 0:
            positions[book, cycle].add(int(position))
    matches = {}
    for title, candidates in titles.items():
        if len(candidates) != 1:
            continue
        book, cycle = next(iter(candidates))
        name = names[cycle]
        if title and name and not re.fullmatch(r"Q[0-9]+", name):
            numbers = positions[book, cycle]
            matches[title] = (name, next(iter(numbers)) if len(numbers) == 1 else 0)
    return matches
