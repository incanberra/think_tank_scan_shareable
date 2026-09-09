"""Build bounded evidence packets from the article's beginning, end and relevant sections."""
import re
import config

TERMS = re.compile(r"sanction|export control|supply.chain|ownership|acquisition|critical mineral|semiconductor|dependenc|vulnerab|coerc|industrial.base|investment", re.I)


def excerpt(text, budget=None):
    budget = budget or config.LLM_ITEM_TEXT_CHAR_LIMIT
    if len(text) <= budget:
        return text
    # Reserve the conclusion before selecting topic-rich passages in the middle.
    edge = max(1, (budget - 150) // 4)
    head, tail = text[:edge], text[-edge:]
    middle = text[edge:-edge]
    chunks = [middle[i:i + 1000] for i in range(0, len(middle), 1000)]
    ranking = sorted(range(len(chunks)), key=lambda i: (len(TERMS.findall(chunks[i])), -i), reverse=True)
    available = budget - len(head) - len(tail) - 150
    selected = {}
    for i in ranking:
        if available <= 0:
            break
        selected[i] = chunks[i][:available]
        available -= len(selected[i]) + 2
    body = "\n\n".join(selected[i] for i in sorted(selected))
    return ("[Article opening]\n" + head + "\n\n[Selected middle sections]\n" + body + "\n\n[Article conclusion]\n" + tail)[:budget]
